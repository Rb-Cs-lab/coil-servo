`timescale 1 ns / 1 ps

// Output stage per docs/design.md Node F: sign-of-u handoff between the
// pass bank (OUT1) and the active clamp (OUT2) with a deadband, plus the
// enable gate. Mutual exclusion is structural: one mux, one source --
// both-active is unrepresentable. Bit-exact mirror of
// model/coil_servo_model/fixed_point.py::output_mux_fixed.
//
// enable low (servo disabled, FSM in IDLE, or reset) forces both outputs
// to exactly zero -- safety invariants 2 and 3.

module servo_output_mux
(
  input  wire               aclk,
  input  wire               aresetn,

  input  wire               enable,
  input  wire signed [13:0] u14,          // clamped PI output
  input  wire        [13:0] deadband,     // handoff deadband, counts
  input  wire               out2_invert,  // flip OUT2 analog polarity

  output reg  signed [13:0] out1,         // pass bank command, >= 0
  output reg  signed [13:0] out2          // clamp command
);

  wire signed [14:0] u15 = u14;
  wire signed [14:0] db  = {1'b0, deadband};
  // -(-8192) does not fit s14: saturate the magnitude at 8191
  wire signed [14:0] mag15 = -u15;
  wire signed [13:0] mag = (mag15 > 15'sd8191) ? 14'sd8191 : mag15[13:0];

  always @(posedge aclk) begin
    if (!aresetn || !enable) begin
      out1 <= 14'sd0;
      out2 <= 14'sd0;
    end else if (u15 > db) begin
      out1 <= u14;
      out2 <= 14'sd0;
    end else if (u15 < -db) begin
      out1 <= 14'sd0;
      out2 <= out2_invert ? -mag : mag;
    end else begin
      out1 <= 14'sd0;
      out2 <= 14'sd0;
    end
  end

endmodule
