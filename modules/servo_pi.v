`timescale 1 ns / 1 ps

// PI core per docs/design.md Nodes D and E. Bit-exact mirror of
// model/coil_servo_model/fixed_point.py::FixedPI -- the cocotb bench
// compares the two tick-for-tick, including the integrator state.
//
// Formats: e s22 Q2.20; gains s18 mantissa + right shift (u5 P / u6 I);
// p/i/u s24 Q3.20; accumulator s48 saturating; output s14 Q1.13 after
// round-to-nearest and the hard clamp (100% of rated current).
//
// Multi-cycle: 4 clocks per tick, sequenced by a tiny FSM. The tick period
// is 128 clocks (decimation ratio), so timing is easy and a tick can never
// arrive while the previous one is still in flight.
//
// clamp must be <= 8191 (Q1.13 positive range); the register map's
// out_clamp is 6554 counts = 100% of rated current.

module servo_pi
(
  input  wire               aclk,
  input  wire               aresetn,

  input  wire               tick,       // one-cycle strobe, e valid with it
  input  wire signed [21:0] e,          // decimated drive-frame error

  input  wire signed [17:0] kp_mant,
  input  wire        [4:0]  kp_shift,
  input  wire signed [17:0] ki_mant,
  input  wire        [5:0]  ki_shift,
  input  wire        [13:0] clamp,      // hard output clamp, counts

  input  wire               int_hold,   // freeze accumulator (safety core)
  input  wire               int_clear,  // zero accumulator (FSM / host)

  output reg  signed [13:0] u14,        // clamped output, DAC counts
  output reg                out_sat,    // clamp engaged on the last tick
  output wire signed [47:0] acc_mon,    // integrator state (STS/debug)
  output wire               int_railed  // accumulator at its s48 rails (STS)
);

  localparam [2:0] S_IDLE = 3'd0, S_MUL = 3'd1, S_SHIFT = 3'd2,
                   S_SUM = 3'd3, S_OUT = 3'd4;

  reg [2:0] state;
  reg signed [21:0] e_r;
  reg signed [39:0] p_full, ki_prod;
  reg signed [23:0] p_term, i_term, u24;
  reg signed [47:0] acc;

  assign acc_mon = acc;
  assign int_railed = (acc == 48'sh7fffffffffff) || (acc == 48'sh800000000000);

  // saturate to s24 (design.md saturation points 1 and 3)
  function signed [23:0] sat24(input signed [48:0] x);
    sat24 = (x > 49'sd8388607)  ? 24'sh7fffff :
            (x < -49'sd8388608) ? 24'sh800000 : x[23:0];
  endfunction

  // saturate to s48 (saturation point 2 -- the integrator never wraps)
  function signed [47:0] sat48(input signed [48:0] x);
    sat48 = (x > 49'sd140737488355327)  ? 48'sh7fffffffffff :
            (x < -49'sd140737488355328) ? 48'sh800000000000 : x[47:0];
  endfunction

  // widened intermediates so no addition can overflow before saturating
  wire signed [24:0] sum_pi   = p_term + i_term;
  wire signed [48:0] acc_next = acc + ki_prod;

  // Q3.20 -> Q1.13: round to nearest, then the hard clamp (Node E)
  wire signed [24:0] u_round  = u24 + 25'sd64;
  wire signed [17:0] u14_pre  = u_round >>> 7;
  wire signed [17:0] clamp_s  = {4'b0000, clamp};
  wire signed [13:0] clamp_pos = clamp[13:0];
  wire engaged = (u14_pre > clamp_s) || (u14_pre < -clamp_s);
  // anti-windup mechanism 1: don't integrate further into the clamp
  wire windup_skip = engaged && ((e_r > 0) == (u24 > 0));

  always @(posedge aclk) begin
    if (!aresetn) begin
      state <= S_IDLE;
      acc <= 48'sd0;
      u14 <= 14'sd0;          // reset state is OFF (safety invariant 2)
      out_sat <= 1'b0;
      e_r <= 22'sd0;
      p_full <= 40'sd0;
      ki_prod <= 40'sd0;
      p_term <= 24'sd0;
      i_term <= 24'sd0;
      u24 <= 24'sd0;
    end else begin
      if (int_clear) acc <= 48'sd0;
      case (state)
        S_IDLE: if (tick) begin
          e_r <= e;
          state <= S_MUL;
        end
        S_MUL: begin
          p_full  <= e_r * kp_mant;
          ki_prod <= e_r * ki_mant;
          state <= S_SHIFT;
        end
        S_SHIFT: begin
          p_term <= sat24(p_full >>> kp_shift);
          i_term <= sat24(acc >>> ki_shift);
          state <= S_SUM;
        end
        S_SUM: begin
          u24 <= sat24(sum_pi);
          state <= S_OUT;
        end
        S_OUT: begin
          u14 <= (u14_pre > clamp_s)  ? clamp_pos :
                 (u14_pre < -clamp_s) ? -clamp_pos : u14_pre[13:0];
          out_sat <= engaged;
          if (!int_hold && !int_clear && !windup_skip)
            acc <= sat48(acc_next);
          state <= S_IDLE;
        end
        default: state <= S_IDLE;
      endcase
    end
  end

endmodule
