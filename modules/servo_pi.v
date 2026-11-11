`timescale 1 ns / 1 ps

// PI core per docs/design.md Nodes D and E. Bit-exact mirror of
// model/coil_servo_model/fixed_point.py::FixedPI -- the cocotb bench
// compares the two tick-for-tick, including the integrator state.
//
// Formats: e s22 Q2.20; gains s18 mantissa + right shift (u5 P / u6 I);
// p/i/u s24 Q3.20; accumulator s48 saturating; output s14 Q1.13 after
// round-to-nearest and the hard clamp (100% of rated current).
//
// Multi-cycle: 5 clocks per tick, sequenced by a tiny FSM. The tick period
// is 128 clocks (decimation ratio), so a tick can never arrive while the
// previous one is still in flight. TIMING (first MVP build failed by
// 1.8 ns): the saturated integrator sum, the output bounding and the
// integrator-floor comparisons are each registered (S_SUM, S_PRE) so the
// final update (S_OUT) is a plain select; the integrator floor acc_lo is
// built in two registered stages. A clear anywhere during a tick's
// processing skips that tick's accumulation (clr_seen) -- precomputed sums
// must never overwrite a cleared integrator. (The bench and FixedPI treat a
// clear as lasting the whole tick, which is equivalent.)
//
// Output bounds: u14 = min(max(u_pre, floor), clamp) -- the clamp wins if
// the two cross. floor is signed: -clamp reproduces the full-architecture
// symmetric bound (OUT2 side); the MVP uses 0, or the RUN-only dead-zone
// floor (CFG u_floor). A non-negative floor also bounds the integrator:
// acc >= floor << (7 + ki_shift) after every non-held tick, so the
// integral term alone holds the output on the floor (falls back to 0 if
// that shift does not fit s48). clamp must be <= 8191.

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
  input  wire signed [13:0] floor,      // lower output bound, counts

  input  wire               int_hold,   // freeze accumulator (safety core)
  input  wire               int_clear,  // zero accumulator (FSM / host)

  output reg  signed [13:0] u14,        // clamped output, DAC counts
  output reg                out_sat,    // clamp engaged on the last tick
  output reg                at_floor,   // output on its lower bound (STS)
  output reg  signed [6:0]  resid,      // rounding residual for the dither
  output wire signed [47:0] acc_mon,    // integrator state (STS/debug)
  output wire               int_railed  // accumulator at its s48 rails (STS)
);

  localparam [2:0] S_IDLE = 3'd0, S_MUL = 3'd1, S_SHIFT = 3'd2,
                   S_SUM = 3'd3, S_PRE = 3'd4, S_OUT = 3'd5;

  reg [2:0] state;
  reg signed [21:0] e_r;
  reg signed [39:0] p_full, ki_prod;
  reg signed [23:0] p_term, i_term, u24;
  reg signed [47:0] acc;
  // pipeline registers (S_SUM / S_PRE)
  reg signed [47:0] acc_sat;
  reg signed [13:0] u14_b;
  reg signed [6:0]  resid_b;
  reg engaged_b, at_floor_b, windup_b, acc_lt, sat_lt, clr_seen;

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
  wire signed [17:0] floor_s  = floor;
  // the clamp wins over a floor configured above it
  wire signed [13:0] floor_eff = (floor_s > clamp_s) ? clamp_pos : floor;
  wire engaged = (u14_pre > clamp_s) || (u14_pre < -clamp_s);
  // anti-windup mechanism 1: don't integrate further into either bound
  wire windup_skip = ((u14_pre > clamp_s) && (e_r > 0)) ||
                     ((u14_pre < floor_s) && (e_r < 0));

  // Integrator lower bound for a non-negative floor, two registered stages
  // (CFG and FSM state change rarely; the tick period is 128 clocks):
  // floor << (7 + ki_shift); 0 if that overflows s48 (any bit >= 47 set).
  wire [83:0] lo_wide = {63'd0, floor[13:0], 7'd0} << ki_shift;
  reg  [83:0] lo_wide_r;
  reg         floor_neg_r;
  reg signed [47:0] acc_lo;
  always @(posedge aclk) begin
    lo_wide_r   <= lo_wide;
    floor_neg_r <= floor[13];
    acc_lo <= floor_neg_r ? 48'sh800000000000 :
              (|lo_wide_r[83:47]) ? 48'sd0 : lo_wide_r[47:0];
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      state <= S_IDLE;
      acc <= 48'sd0;
      u14 <= 14'sd0;          // reset state is OFF (safety invariant 2)
      out_sat <= 1'b0;
      at_floor <= 1'b0;
      resid <= 7'sd0;
      e_r <= 22'sd0;
      p_full <= 40'sd0;
      ki_prod <= 40'sd0;
      p_term <= 24'sd0;
      i_term <= 24'sd0;
      u24 <= 24'sd0;
      acc_sat <= 48'sd0;
      u14_b <= 14'sd0;
      resid_b <= 7'sd0;
      engaged_b <= 1'b0;
      at_floor_b <= 1'b0;
      windup_b <= 1'b0;
      acc_lt <= 1'b0;
      sat_lt <= 1'b0;
      clr_seen <= 1'b0;
    end else begin
      if (int_clear) begin
        acc <= 48'sd0;
        clr_seen <= 1'b1;
      end
      case (state)
        S_IDLE: if (tick) begin
          e_r <= e;
          clr_seen <= int_clear;    // a new tick starts its own record
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
          acc_sat <= sat48(acc_next);
          state <= S_PRE;
        end
        S_PRE: begin
          u14_b <= (u14_pre > clamp_s)  ? clamp_pos :
                   (u14_pre < floor_s)  ? floor_eff : u14_pre[13:0];
          engaged_b <= engaged;
          at_floor_b <= (u14_pre <= floor_s);
          // the 7 bits rounding dropped (u24/128 = u14_pre + resid/128);
          // zero when an output bound is engaged
          resid_b <= ((u14_pre > clamp_s) || (u14_pre < floor_s)) ? 7'sd0 :
                     $signed({1'b0, u_round[6:0]}) - 8'sd64;
          windup_b <= windup_skip;
          acc_lt <= (acc < acc_lo);
          sat_lt <= (acc_sat < acc_lo);
          state <= S_OUT;
        end
        S_OUT: begin
          u14 <= u14_b;
          out_sat <= engaged_b;
          at_floor <= at_floor_b;
          resid <= resid_b;
          // acc = max(windup ? acc : sat48(acc + ki*e), acc_lo)
          if (!int_hold && !int_clear && !clr_seen)
            acc <= windup_b ? (acc_lt ? acc_lo : acc)
                            : (sat_lt ? acc_lo : acc_sat);
          state <= S_IDLE;
        end
        default: state <= S_IDLE;
      endcase
    end
  end

endmodule
