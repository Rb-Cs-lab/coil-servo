`timescale 1 ns / 1 ps

// OUT1 resolution dither (MVP; register_map.md ctrl b20 dither_enable).
// Bit-exact mirror of model/coil_servo_model/fixed_point.py::SigmaDelta.
//
// Why: with the gain-8 op-amp stage one DAC count is ~0.12 A of coil
// current, so a steady hold hunts between two adjacent codes (~50 mA p-p).
// The PI computes 7 bits finer than the DAC (u24 vs u14) and rounding drops
// them; here a first-order sigma-delta, running every 8 ns clock, puts them
// back as a +-1-count toggle whose mean is resid/128. The gate (24.9 ohm
// into the FET input capacitance, ~0.6 us) averages it, so the pass bank
// sees the PI output at ~2^-7 count resolution. Unverified on hardware:
// the DAC's linearity and the op-amp's response to a 1-count toggle at
// tens of MHz set the real benefit (bench measurement).
//
// The dithered value is clipped to [lo, clamp] -- it never exceeds the hard
// clamp (safety invariant 1). enable low: output = u14 exactly, sigma-delta
// state cleared. The output is registered (timing): it reflects the inputs
// and sigma-delta state of the previous clock.

module servo_dither
(
  input  wire               aclk,
  input  wire               aresetn,

  input  wire               enable,
  input  wire signed [13:0] u14,        // PI output, counts
  input  wire signed [6:0]  resid,      // rounding residual, [-64, 63]
  input  wire signed [13:0] lo,         // PI lower bound (floor)
  input  wire        [13:0] clamp,      // hard clamp, counts (<= 8191)

  output reg  signed [13:0] u_out
);

  reg  signed [7:0] acc;                // sigma-delta state, [-64, 63]
  wire signed [8:0] s = acc + resid;
  wire up   = (s >= 9'sd64);
  wire down = (s < -9'sd64);

  // TIMING (rev 5 snap-in-flip build, 2026-11-13: -50 ps, 13 logic levels
  // acc -> u_out): the old form added the +-1 decision to u14 and only then
  // clipped, so the sigma-delta adder, the 15-bit adder and both clip
  // comparators sat in series. Now the three possible outputs (u14 - 1,
  // u14, u14 + 1, each clipped to [lo, clamp]) are formed in parallel with
  // the sigma-delta step, and up/down only select among them. Same function
  // on every clock (the bit-exact bench against SigmaDelta is unchanged).
  wire signed [14:0] c15  = {1'b0, clamp};
  wire signed [14:0] lo15 = (lo > $signed(c15)) ? c15 : lo;
  wire signed [14:0] u_0  = u14;
  wire signed [14:0] u_p  = u_0 + 15'sd1;
  wire signed [14:0] u_m  = u_0 - 15'sd1;
  wire signed [14:0] c_0  = (u_0 > c15) ? c15 : (u_0 < lo15) ? lo15 : u_0;
  wire signed [14:0] c_p  = (u_p > c15) ? c15 : (u_p < lo15) ? lo15 : u_p;
  wire signed [14:0] c_m  = (u_m > c15) ? c15 : (u_m < lo15) ? lo15 : u_m;
  wire signed [14:0] u_c  = up ? c_p : down ? c_m : c_0;

  always @(posedge aclk) begin
    u_out <= !aresetn ? 14'sd0 : (enable ? u_c[13:0] : u14);
    if (!aresetn || !enable)
      acc <= 8'sd0;
    else
      acc <= up ? s - 9'sd128 : down ? s + 9'sd128 : s;
  end

endmodule
