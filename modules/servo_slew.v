`timescale 1 ns / 1 ps

// Setpoint slew-rate limiter (MVP rewrite step 3; register_map.md W15/W20).
// Bit-exact mirror of model/coil_servo_model/fixed_point.py::SlewLimiter.
//
// Why: on the MVP plant a large setpoint STEP misbehaves in both
// directions -- up-steps that are 5 V-compliance-limited overshoot (the
// integrator winds up during the rise), and down-steps undershoot (it winds
// down during the slow freewheel). Ramping at <= 200 A/ms up and <= 50 A/ms
// down avoids both (mvp_architecture.md 3.1), so the firmware enforces it.
//
// "Up" = moving away from zero (|sp| growing), "down" = toward zero; a
// move across zero uses the down rate to zero, then the up rate. Rates are
// in 2^-14 counts per 8 ns clock (= 2^-7 counts per 1.024 us PI tick), so
// 200 A/ms at I_FS = 200 A is 1074. A rate of 0 freezes the setpoint (the
// servo does nothing until configured -- fail-safe).
//
// SNAP: a target inside the zero window (|target| < snap_win, the flip
// zero window zero_win) is taken at once, unlimited. A step to zero is then
// a true snap-off -- the coil freewheels at full speed -- and the down-limit,
// whose job is to prevent undershoot, is not needed there: the current
// cannot go below zero (decided 2026-11-10).
//
// hold_zero (FSM forced zero: IDLE, ramp-down, flip, trip) drops the output
// to 0 at once and keeps it there, so after a re-arm or a flip the setpoint
// ramps up from 0. bypass (open-loop transfer-function mode) passes the
// target straight through.

module servo_slew
(
  input  wire               aclk,
  input  wire               aresetn,

  input  wire signed [13:0] target,     // selected setpoint (IN2 or CFG)
  input  wire               hold_zero,  // FSM forced zero
  input  wire               bypass,     // open loop: no limiting
  input  wire        [15:0] rate_up,    // away from zero, 2^-14 counts/clk
  input  wire        [15:0] rate_down,  // toward zero, 2^-14 counts/clk
  input  wire        [13:0] snap_win,   // |target| below this: no limit

  output wire signed [13:0] sp_out      // limited setpoint, counts
);

  reg  signed [27:0] acc;               // setpoint, counts << 14
  wire signed [27:0] t     = {target, 14'd0};
  wire signed [28:0] delta = t - acc;
  wire away = ((acc >= 0) && (delta > 0)) || ((acc <= 0) && (delta < 0));
  wire signed [28:0] r = {13'd0, away ? rate_up : rate_down};
  wire signed [14:0] t15 = target;
  wire        [14:0] abs_t = (t15 < 0) ? -t15 : t15;
  wire snap = abs_t < {1'b0, snap_win};

  assign sp_out = acc[27:14];           // floor; exact once on target

  always @(posedge aclk) begin
    if (!aresetn || hold_zero)
      acc <= 28'sd0;
    else if (bypass || snap)
      acc <= t;
    else if (delta > r)
      acc <= acc + r[27:0];
    else if (delta < -r)
      acc <= acc - r[27:0];
    else
      acc <= t;
  end

endmodule
