`timescale 1 ns / 1 ps

// Error path per docs/design.md Node B: setpoint mux (analog IN2 / CFG
// register / FSM-forced zero) and bridge-frame polarity rotation
//   e_drive = pol * (sp - meas)
// Bit-exact mirror of
// model/coil_servo_model/fixed_point.py::drive_frame_error.
//
// sp_sign_mismatch flags a setpoint whose sign disagrees with the bridge
// polarity by more than MISMATCH_THR counts. The condition is safe (the
// loop drives toward zero) but indicates a control-system misconfiguration
// the host must warn about.

module servo_error
(
  input  wire               aclk,
  input  wire               aresetn,

  input  wire signed [13:0] meas,           // IN1 code
  input  wire signed [13:0] in2,            // IN2 code (analog setpoint)
  input  wire signed [13:0] sp_reg,         // CFG setpoint register
  input  wire               sp_source,      // 0 = IN2, 1 = sp_reg
  input  wire               sp_force_zero,  // FSM override
  input  wire               polarity,       // bridge polarity bit

  output reg  signed [14:0] e_fast,          // drive-frame error, s15
  output reg  signed [13:0] sp_active,       // setpoint after the mux (STS)
  output reg                sp_sign_mismatch
);

  localparam signed [13:0] MISMATCH_THR = 14'sd64;   // ~1% of full scale

  wire signed [13:0] sp = sp_force_zero ? 14'sd0 :
                          (sp_source ? sp_reg : in2);
  // |e| <= 16383, so the s15 negation below cannot overflow
  wire signed [14:0] e = sp - meas;

  always @(posedge aclk) begin
    if (!aresetn) begin
      e_fast <= 15'sd0;
      sp_active <= 14'sd0;
      sp_sign_mismatch <= 1'b0;
    end else begin
      e_fast <= polarity ? -e : e;
      sp_active <= sp;
      sp_sign_mismatch <= polarity ? (sp > MISMATCH_THR)
                                   : (sp < -MISMATCH_THR);
    end
  end

endmodule
