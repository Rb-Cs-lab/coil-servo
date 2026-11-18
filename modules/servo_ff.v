`timescale 1 ns / 1 ps

// Setpoint feedforward (register map rev 7; docs/register_map.md ctrl b25,
// b29:26, words 24-31). Bit-exact mirror of
// model/coil_servo_model/fixed_point.py::ff_interp.
//
// The pass bank is a voltage-controlled current source with a dead zone and
// a strongly current-dependent gain, so after a setpoint step the PI has to
// find the right OUT1 by integrating -- slow with gains low enough to stay
// stable at the top of the range. This block looks the OUT1 command for the
// requested current up in a 16-point table measured on the hardware (host
// coil_servo_host.ffcal) and the PI adds only the correction.
//
// x = max(sp, 0) (drive frame: the bank only sources), breakpoints every
// 2^shift setpoint counts: point k sits at x = k << shift. Between points
// the value is interpolated linearly:
//   k = x >> shift, f = x & (2^shift - 1)
//   ff = T[k] + (((T[k+1] - T[k]) * f) >>> shift)      (k < 15)
//   ff = T[15]                                          (k >= 15)
// then limited to 0..8191. Pipeline: 4 clocks, nothing against the 128-clock
// PI tick (servo_pi samples ff with the tick). enable = 0 gives exactly 0.

module servo_ff
(
  input  wire               aclk,
  input  wire               aresetn,
  input  wire               enable,
  input  wire signed [13:0] sp,        // drive-frame setpoint, counts
  input  wire        [3:0]  shift,     // breakpoint spacing 2^shift counts
  input  wire        [223:0] table_flat, // T[k] = table_flat[14k+13 : 14k], u14
  output reg         [13:0] ff         // feedforward, OUT1 counts, 0..8191
);

  // stage 1: segment index and fraction
  wire [12:0] x = sp[13] ? 13'd0 : sp[12:0];
  wire [12:0] idx_w = x >> shift;
  wire [12:0] mask = ~(13'h1fff << shift);
  reg  [3:0]  k1;
  reg  [12:0] f1;
  reg         top1, en1;
  reg  [3:0]  sh1;
  always @(posedge aclk) begin
    top1 <= (idx_w >= 13'd15);
    k1   <= (idx_w >= 13'd15) ? 4'd15 : idx_w[3:0];
    f1   <= (idx_w >= 13'd15) ? 13'd0 : (x & mask);
    sh1  <= shift;
    en1  <= aresetn & enable;
  end

  // stage 2: the two table points around x
  wire [13:0] t_k  = table_flat[k1*14 +: 14];
  wire [13:0] t_k1 = table_flat[(top1 ? k1 : k1 + 4'd1)*14 +: 14];
  reg  [13:0] a2;
  reg  signed [14:0] d2;
  reg  [12:0] f2;
  reg  [3:0]  sh2;
  reg         en2;
  always @(posedge aclk) begin
    a2  <= t_k;
    d2  <= $signed({1'b0, t_k1}) - $signed({1'b0, t_k});
    f2  <= f1;
    sh2 <= sh1;
    en2 <= en1;
  end

  // stage 3: slope x fraction (s15 x u13 -> s29)
  reg  signed [28:0] p3;
  reg  [13:0] a3;
  reg  [3:0]  sh3;
  reg         en3;
  always @(posedge aclk) begin
    p3  <= d2 * $signed({1'b0, f2});
    a3  <= a2;
    sh3 <= sh2;
    en3 <= en2;
  end

  // stage 4: add, limit to 0..8191
  wire signed [28:0] step4 = p3 >>> sh3;
  wire signed [29:0] sum4  = $signed({16'd0, a3}) + step4;
  always @(posedge aclk) begin
    if (!aresetn || !en3)
      ff <= 14'd0;
    else
      ff <= (sum4 < 0) ? 14'd0 : (sum4 > 30'sd8191) ? 14'd8191 : sum4[13:0];
  end

endmodule
