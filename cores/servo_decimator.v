`timescale 1 ns / 1 ps

// Boxcar accumulate-and-dump decimator, ratio 128 (docs/design.md Node C).
// Sums 128 fast-rate s15 error samples into an exact s22 result: no
// rounding, no saturation possible by width (128 * 2^14 = 2^21).
// Bit-exact mirror of model/coil_servo_model/fixed_point.py::Decimator.
//
// out_valid strobes for one clock when e_out updates -- this is the PI tick.

module servo_decimator
(
  input  wire               aclk,
  input  wire               aresetn,

  input  wire               in_valid,   // fast sample strobe
  input  wire signed [14:0] e_fast,     // fast-rate error, s15

  output reg  signed [21:0] e_out,      // decimated error, s22 Q2.20
  output reg                out_valid
);

  reg signed [21:0] acc;
  reg [6:0] count;

  always @(posedge aclk) begin
    if (!aresetn) begin
      acc <= 22'sd0;
      count <= 7'd0;
      e_out <= 22'sd0;
      out_valid <= 1'b0;
    end else begin
      out_valid <= 1'b0;
      if (in_valid) begin
        if (count == 7'd127) begin
          e_out <= acc + e_fast;
          out_valid <= 1'b1;
          acc <= 22'sd0;
          count <= 7'd0;
        end else begin
          acc <= acc + e_fast;
          count <= count + 7'd1;
        end
      end
    end
  end

endmodule
