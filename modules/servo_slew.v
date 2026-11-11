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
//
// TIMING (first MVP build: -2.2 ns on the one-clock version, ADC IN2 ->
// acc): the target and the snap test are registered first (one clock of
// latency, nothing against the 1 us PI tick), and the limiting step is
// split over two clocks -- phase 0 measures delta = target - acc, phase 1
// moves acc by 2*rate (or onto the target). The average rate is unchanged;
// every register-to-register path is one adder or comparator deep. A
// hold/bypass/snap in phase 0 invalidates that measurement, so phase 1
// never applies a stale step.

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

  // input stage
  wire signed [14:0] t15   = target;
  wire        [14:0] abs_t = (t15 < 0) ? -t15 : t15;
  reg  signed [27:0] t_r;               // target, counts << 14
  reg                snap_r;

  // two-phase limiter
  reg  signed [27:0] acc;               // setpoint, counts << 14
  reg  signed [27:0] t_p;               // target as measured in phase 0
  reg  signed [28:0] delta_r;           // t_p - acc, measured in phase 0
  reg                phase, valid;

  wire away = ((acc >= 0) && (delta_r > 0)) || ((acc <= 0) && (delta_r < 0));
  wire signed [28:0] r2 = {12'd0, (away ? rate_up : rate_down), 1'b0};
  wire signed [27:0] acc_up = acc + r2[27:0];
  wire signed [27:0] acc_dn = acc - r2[27:0];

  assign sp_out = acc[27:14];           // floor; exact once on target

  always @(posedge aclk) begin
    if (!aresetn) begin
      t_r <= 28'sd0;
      snap_r <= 1'b0;
      acc <= 28'sd0;
      t_p <= 28'sd0;
      delta_r <= 29'sd0;
      phase <= 1'b0;
      valid <= 1'b0;
    end else begin
      t_r <= {target, 14'd0};
      snap_r <= abs_t < {1'b0, snap_win};
      phase <= ~phase;

      if (hold_zero) begin
        acc <= 28'sd0;
        valid <= 1'b0;
      end else if (bypass || snap_r) begin
        acc <= t_r;
        valid <= 1'b0;
      end else if (!phase) begin
        delta_r <= t_r - acc;           // measure
        t_p <= t_r;
        valid <= 1'b1;
      end else begin
        valid <= 1'b0;
        if (valid)                      // move
          acc <= (delta_r > r2)  ? acc_up :
                 (delta_r < -r2) ? acc_dn : t_p;
      end
    end
  end

endmodule
