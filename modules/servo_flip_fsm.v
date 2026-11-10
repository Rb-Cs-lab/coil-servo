`timescale 1 ns / 1 ps

// H-bridge flip state machine per docs/design.md section 3 and BOOTSTRAP.
//
// The invariant this module exists to enforce: THE BRIDGE POLARITY NEVER
// CHANGES WITH CURRENT FLOWING. A flip request drives the setpoint to zero,
// waits for the measured current to sit inside a zero window for a
// qualified number of decimated samples, opens the bridge, waits the dead
// time, toggles polarity, waits again, re-closes, clears the integrator,
// and waits a settle delay (chamber eddy currents -- unmeasured, register
// placeholder) before releasing the setpoint.
//
// If the zero window is never reached within flip_timeout, the FSM parks in
// TIMEOUT_HOLD -- still servoing toward zero, bridge still closed, no flip
// -- until the host acknowledges. It never proceeds at nonzero current.
//
// servo_enable 1->0 performs a GRACEFUL STOP through the same path: ramp to
// zero, open the bridge, park in IDLE. It never opens the bridge at current.
// The arm input (DIO4) gates IDLE->RUN and flip requests only; a falling
// edge mid-shot does not kill the bridge (that is the interlock's job).
// Leaving IDLE needs a RISING EDGE of arm while servo_enable is set: an arm
// level already high at bitstream load (armed_prev resets to 1), after a
// graceful stop or after a trip does not count (MVP rewrite step 3).
//
// OVER-CURRENT TRIP (MVP): in every state except IDLE, |i_meas| above
// trip_level for trip_ticks consecutive decimated samples enters TRIP:
// out_kill forces both DAC outputs to zero at once, the setpoint is forced
// to zero and the integrator frozen, but the bridge is left as it is --
// closed if it was closed, so the coil freewheels down instead of pushing
// its energy into the supply. Once the current qualifies the zero window
// (or after trip_timeout; 0 = at once) the bridge opens and the FSM latches
// in TRIPPED until the host pulses trip_ack (-> IDLE, which then needs a
// fresh arm edge). The trip takes priority over everything else.
//
// int_hold is asserted while the bridge is open (IDLE/DISABLE/FLIP) and in
// the trip states; in RAMP_DOWN/SETTLE/TIMEOUT_HOLD the loop is actively
// servoing to zero and needs its integrator.
//
// State encoding matches docs/register_map.md.

module servo_flip_fsm
(
  input  wire               aclk,
  input  wire               aresetn,

  input  wire               servo_enable,   // CFG ctrl b0
  input  wire               armed,          // DIO4, conditioned
  input  wire               flip_req,       // one-cycle pulse, conditioned
  input  wire               flip_fault_ack, // CFG ctrl b7
  input  wire               trip_ack,       // CFG ctrl b19

  input  wire               tick,           // decimated-rate strobe
  input  wire signed [21:0] i_meas,         // decimated measured current

  input  wire        [13:0] zero_win,       // window, counts (PROVISIONAL)
  input  wire        [15:0] zero_holdoff,   // ticks the window must hold
  input  wire        [15:0] deadtime,       // 8 ns ticks (PROVISIONAL)
  input  wire        [31:0] settle,         // 8 ns ticks (eddy PLACEHOLDER)
  input  wire        [31:0] flip_timeout,   // 8 ns ticks, 0 = disabled
  input  wire        [13:0] trip_level,     // counts of I_FS
  input  wire        [15:0] trip_ticks,     // decimated samples (0 = 1)
  input  wire        [31:0] trip_timeout,   // 8 ns ticks, 0 = open at once

  output reg                bridge_en,      // DIO1 (low = all FETs off)
  output reg                polarity,       // DIO0
  output reg                sp_force_zero,  // to servo_error
  output reg                int_clear,      // one-cycle pulse to servo_pi
  output reg                int_hold,       // to servo_pi
  output reg                out_kill,       // force both DACs to zero
  output wire               run_state,      // in RUN (u_floor applies)
  output wire        [3:0]  fsm_state,      // STS
  output wire               timeout_hold,   // STS: parked in TIMEOUT_HOLD
  output wire               tripped         // STS: in TRIP or TRIPPED
);

  localparam [3:0] S_IDLE = 4'd0, S_RUN = 4'd1, S_RAMP = 4'd2,
                   S_DIS = 4'd3, S_FLIP = 4'd4, S_EN = 4'd5,
                   S_SETTLE = 4'd6, S_THOLD = 4'd7,
                   S_TRIP = 4'd8, S_TRIPPED = 4'd9;

  reg [3:0] state;
  reg stop_req;              // this RAMP_DOWN is a graceful stop, not a flip
  reg [31:0] timer;
  reg [15:0] win_cnt;
  reg [15:0] trip_cnt;
  reg armed_prev;

  assign fsm_state = state;
  assign timeout_hold = (state == S_THOLD);
  assign tripped = (state == S_TRIP) || (state == S_TRIPPED);
  assign run_state = (state == S_RUN);

  wire arm_rise = armed & ~armed_prev;

  // |i_meas| < zero_win, compared at the decimated resolution (Q2.20):
  // zero_win is in Q1.13 counts, so scale by 2^7.
  wire signed [22:0] i_ext = i_meas;
  wire signed [22:0] abs_i = (i_ext < 0) ? -i_ext : i_ext;
  wire in_window = abs_i < {2'b00, zero_win, 7'b0000000};
  wire window_qualified = tick && in_window &&
      ({16'd0, win_cnt} + 32'd1 >= {16'd0, zero_holdoff});

  // over-current: |i| > trip_level for trip_ticks consecutive samples
  wire over = abs_i > {2'b00, trip_level, 7'b0000000};
  wire trip_watch = (state != S_IDLE) && !tripped;
  wire trip_fire = trip_watch && tick && over &&
      ({1'b0, trip_cnt} + 17'd1 >= {1'b0, trip_ticks});

  always @(posedge aclk) begin
    if (!aresetn) begin
      state <= S_IDLE;
      bridge_en <= 1'b0;          // reset state is OFF (safety invariant 2)
      polarity <= 1'b0;
      sp_force_zero <= 1'b1;
      int_clear <= 1'b0;
      int_hold <= 1'b1;
      out_kill <= 1'b0;
      stop_req <= 1'b0;
      timer <= 32'd0;
      win_cnt <= 16'd0;
      trip_cnt <= 16'd0;
      armed_prev <= 1'b1;         // a level high at load is not an edge
    end else begin
      int_clear <= 1'b0;          // default: pulses last one cycle
      armed_prev <= armed;
      if (!trip_watch)
        trip_cnt <= 16'd0;
      else if (tick)
        trip_cnt <= !over ? 16'd0 :
                    (trip_cnt == 16'hffff) ? trip_cnt : trip_cnt + 16'd1;

      if (trip_fire) begin
        // over-current: outputs to zero now, bridge left as it is
        state <= S_TRIP;
        out_kill <= 1'b1;
        sp_force_zero <= 1'b1;
        int_hold <= 1'b1;
        timer <= 32'd0;
        win_cnt <= 16'd0;
      end else if (!servo_enable &&
          (state == S_RUN || state == S_SETTLE || state == S_THOLD)) begin
        // graceful stop: servo to zero first, open the bridge after
        state <= S_RAMP;
        stop_req <= 1'b1;
        sp_force_zero <= 1'b1;
        timer <= 32'd0;
        win_cnt <= 16'd0;
      end else begin
        case (state)

          S_IDLE: begin
            bridge_en <= 1'b0;
            sp_force_zero <= 1'b1;
            int_hold <= 1'b1;
            out_kill <= 1'b0;
            stop_req <= 1'b0;
            if (servo_enable && arm_rise) begin
              state <= S_RUN;
              bridge_en <= 1'b1;
              sp_force_zero <= 1'b0;
              int_hold <= 1'b0;
              int_clear <= 1'b1;
            end
          end

          S_RUN: begin
            if (flip_req && armed) begin
              state <= S_RAMP;
              stop_req <= 1'b0;
              sp_force_zero <= 1'b1;
              timer <= 32'd0;
              win_cnt <= 16'd0;
            end
          end

          S_RAMP: begin
            // loop actively servos to zero; qualify the window over
            // zero_holdoff consecutive decimated samples (noise immunity)
            if (window_qualified) begin
              state <= S_DIS;
              bridge_en <= 1'b0;
              int_hold <= 1'b1;
              timer <= 32'd0;
            end else begin
              if (tick)
                win_cnt <= in_window ? win_cnt + 16'd1 : 16'd0;
              if (!stop_req && flip_timeout != 32'd0 && timer >= flip_timeout)
                state <= S_THOLD;
              else
                timer <= timer + 32'd1;
            end
          end

          S_DIS: begin
            if (timer >= {16'd0, deadtime}) begin
              if (stop_req)
                state <= S_IDLE;         // graceful stop ends here
              else begin
                state <= S_FLIP;
                polarity <= ~polarity;   // bridge is open: safe to toggle
                timer <= 32'd0;
              end
            end else
              timer <= timer + 32'd1;
          end

          S_FLIP: begin
            if (timer >= {16'd0, deadtime}) begin
              state <= S_EN;
              bridge_en <= 1'b1;
              int_clear <= 1'b1;         // restart the integrator clean
              int_hold <= 1'b0;
              timer <= 32'd0;
            end else
              timer <= timer + 32'd1;
          end

          S_EN: state <= S_SETTLE;

          S_SETTLE: begin
            // servoing at zero while chamber eddy currents die out
            if (timer >= settle) begin
              state <= S_RUN;
              sp_force_zero <= 1'b0;
            end else
              timer <= timer + 32'd1;
          end

          S_THOLD: begin
            // never reached zero: keep servoing toward it, bridge stays
            // closed, no flip. Host must acknowledge to resume.
            if (flip_fault_ack) begin
              state <= S_RUN;
              sp_force_zero <= 1'b0;
            end
          end

          S_TRIP: begin
            // coil freewheels with both DACs at zero; open the bridge once
            // the current is inside the zero window, or after trip_timeout
            if (window_qualified || timer >= trip_timeout) begin
              state <= S_TRIPPED;
              bridge_en <= 1'b0;
            end else begin
              if (tick)
                win_cnt <= in_window ? win_cnt + 16'd1 : 16'd0;
              timer <= timer + 32'd1;
            end
          end

          S_TRIPPED: begin
            // latched with the bridge open; only the host acknowledges
            bridge_en <= 1'b0;
            if (trip_ack)
              state <= S_IDLE;
          end

          default: state <= S_IDLE;
        endcase
      end
    end
  end

endmodule
