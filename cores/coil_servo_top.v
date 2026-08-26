`timescale 1 ns / 1 ps

// Coil servo integration top: everything between the ADC stream and the
// DAC stream / E1 pins lives here, in plain Verilog, so the whole signal
// path is verified by the cocotb bench (sim/tb_coil_servo_top.py) before
// Vivado ever sees it. The block design only plumbs this core to the PS,
// axi_hub, ADC/DAC interfaces, capture FIFO, and XADC.
//
// Submodules live in modules/ (scripts/core.tcl packages cores/<name>.v
// together with all of modules/*.v, the upstream pattern for hierarchical
// cores).
//
// CFG/STS field layout: docs/register_map.md is the single source of truth;
// model/coil_servo_model/registers.py mirrors it for the bench and host.

module coil_servo_top #
(
  parameter integer HB_DIV_LOG2 = 16   // heartbeat at 125 MHz / 2^17 ~ 954 Hz
)
(
  input  wire         aclk,
  input  wire         aresetn,

  // ADC stream in: [13:0] IN1 = measured current, [29:16] IN2 = setpoint
  input  wire [31:0]  s_axis_tdata,
  input  wire         s_axis_tvalid,

  // DAC stream out: [13:0] OUT1 = pass bank, [29:16] OUT2 = clamp
  output wire [31:0]  m_axis_tdata,
  output wire         m_axis_tvalid,

  // raw ADC monitor stream out (to the capture FIFO)
  output wire [31:0]  m01_axis_tdata,
  output wire         m01_axis_tvalid,

  // registers (axi_hub port 0 / port 1)
  input  wire [511:0] cfg_data,
  output wire [159:0] sts_data,

  // E1 digital inputs (raw pins, conditioned here)
  input  wire         flip_req_i,   // DIO3_P
  input  wire         arm_i,        // DIO4_P
  input  wire         fault_i,      // DIO5_P (report only, no clear path)

  // E1 digital outputs
  output wire         bridge_polarity_o,  // DIO0_P
  output wire         bridge_enable_o,    // DIO1_P (low = all FETs off)
  output reg          boost_o,            // DIO2_P
  output wire         heartbeat_o,        // DIO6_P

  output wire [7:0]   led_o,
  output wire         fifo_resetn         // capture FIFO reset (active low)
);

  // ------------------------------------------------------------------
  // CFG fields (docs/register_map.md; word n = cfg_data[32n+31 : 32n])
  // ------------------------------------------------------------------
  wire        servo_enable   = cfg_data[0];
  wire        int_clear_host = cfg_data[1];
  wire        sp_source      = cfg_data[2];
  wire        fifo_rst       = cfg_data[3];
  wire        out2_invert    = cfg_data[4];
  wire        boost_mode     = cfg_data[5];
  wire        boost_manual   = cfg_data[6];
  wire        flip_fault_ack = cfg_data[7];
  assign      led_o          = cfg_data[15:8];
  wire        open_loop      = cfg_data[16];  // route setpoint directly to
                                              // the output stage (HIL TF
                                              // measurement; clamp, mux and
                                              // bridge gating still apply)
  wire        capture_sel    = cfg_data[17];  // FIFO: 0 = raw ADC @125 MS/s,
                                              // 1 = decimated {e, i} pairs
  wire signed [13:0] sp_reg       = cfg_data[45:32];    // W1
  wire signed [17:0] kp_mant      = cfg_data[81:64];    // W2
  wire        [4:0]  kp_shift     = cfg_data[100:96];   // W3
  wire signed [17:0] ki_mant      = cfg_data[145:128];  // W4
  wire        [5:0]  ki_shift     = cfg_data[165:160];  // W5
  wire        [13:0] out_clamp    = cfg_data[205:192];  // W6
  wire        [13:0] deadband     = cfg_data[237:224];  // W7
  wire        [13:0] zero_win     = cfg_data[269:256];  // W8
  wire        [15:0] zero_holdoff = cfg_data[303:288];  // W9
  wire        [15:0] deadtime     = cfg_data[335:320];  // W10
  wire        [31:0] settle       = cfg_data[383:352];  // W11
  wire        [31:0] flip_timeout = cfg_data[415:384];  // W12
  wire        [2:0]  dio_invert   = cfg_data[418:416];  // W13

  assign fifo_resetn = aresetn & ~fifo_rst;

  // ------------------------------------------------------------------
  // E1 input conditioning: 2FF synchronizer, programmable inversion,
  // rising-edge pulse for the flip request
  // ------------------------------------------------------------------
  reg [2:0] dio_meta, dio_sync;
  reg       flip_prev;
  always @(posedge aclk) begin
    dio_meta <= {fault_i, arm_i, flip_req_i};
    dio_sync <= dio_meta ^ dio_invert;
    flip_prev <= dio_sync[0];
  end
  wire flip_pulse = dio_sync[0] & ~flip_prev;
  wire armed = dio_sync[1];
  wire fault = dio_sync[2];

  // ------------------------------------------------------------------
  // ADC unpack + monitor passthrough
  // ------------------------------------------------------------------
  wire signed [13:0] meas_code = s_axis_tdata[13:0];
  wire signed [13:0] in2_code  = s_axis_tdata[29:16];

  // capture source: raw ADC every fast sample, or decimated
  // {error[21:6], measured[21:6]} once per PI tick (16.8 ms per 16384-deep
  // FIFO fill -- long enough to see the 100 Hz plant pole)
  assign m01_axis_tdata  = capture_sel ? {e_dec[21:6], i_dec[21:6]}
                                       : s_axis_tdata;
  assign m01_axis_tvalid = capture_sel ? tick_i : s_axis_tvalid;

  // ------------------------------------------------------------------
  // Error path -> decimators (error and measured current run in lockstep)
  // ------------------------------------------------------------------
  wire sp_force_zero, polarity;
  wire signed [14:0] e_fast;
  wire signed [13:0] sp_active;
  wire sp_sign_mismatch;

  servo_error err_0 (
    .aclk(aclk), .aresetn(aresetn),
    .meas(meas_code), .in2(in2_code), .sp_reg(sp_reg),
    .sp_source(sp_source), .sp_force_zero(sp_force_zero),
    .polarity(polarity),
    .e_fast(e_fast), .sp_active(sp_active),
    .sp_sign_mismatch(sp_sign_mismatch)
  );

  // servo_error registers its outputs: delay valid and meas by one clock so
  // both decimators see sample-aligned inputs
  reg valid_r;
  reg signed [14:0] meas_r;
  always @(posedge aclk) begin
    valid_r <= aresetn & s_axis_tvalid;
    meas_r <= meas_code;
  end

  wire signed [21:0] e_dec, i_dec;
  wire tick_e, tick_i;

  servo_decimator dec_e (
    .aclk(aclk), .aresetn(aresetn),
    .in_valid(valid_r), .e_fast(e_fast),
    .e_out(e_dec), .out_valid(tick_e)
  );

  servo_decimator dec_i (
    .aclk(aclk), .aresetn(aresetn),
    .in_valid(valid_r), .e_fast(meas_r),
    .e_out(i_dec), .out_valid(tick_i)
  );

  // ------------------------------------------------------------------
  // Flip FSM
  // ------------------------------------------------------------------
  wire bridge_en, fsm_int_clear, int_hold;
  wire [3:0] fsm_state;
  wire timeout_hold;

  servo_flip_fsm fsm_0 (
    .aclk(aclk), .aresetn(aresetn),
    .servo_enable(servo_enable), .armed(armed),
    .flip_req(flip_pulse), .flip_fault_ack(flip_fault_ack),
    .tick(tick_i), .i_meas(i_dec),
    .zero_win(zero_win), .zero_holdoff(zero_holdoff),
    .deadtime(deadtime), .settle(settle), .flip_timeout(flip_timeout),
    .bridge_en(bridge_en), .polarity(polarity),
    .sp_force_zero(sp_force_zero), .int_clear(fsm_int_clear),
    .int_hold(int_hold),
    .fsm_state(fsm_state), .timeout_hold(timeout_hold)
  );

  assign bridge_polarity_o = polarity;
  assign bridge_enable_o = bridge_en;

  // ------------------------------------------------------------------
  // PI and output stage
  // ------------------------------------------------------------------
  wire signed [13:0] u14;
  wire out_sat, int_railed;
  wire signed [47:0] acc_mon;

  servo_pi pi_0 (
    .aclk(aclk), .aresetn(aresetn),
    .tick(tick_e), .e(e_dec),
    .kp_mant(kp_mant), .kp_shift(kp_shift),
    .ki_mant(ki_mant), .ki_shift(ki_shift),
    .clamp(out_clamp),
    .int_hold(int_hold), .int_clear(fsm_int_clear | int_clear_host),
    .u14(u14), .out_sat(out_sat), .acc_mon(acc_mon),
    .int_railed(int_railed)
  );

  // open-loop mode (HIL transfer-function measurement): the setpoint --
  // IN2 from a lab function generator, or the register -- drives the output
  // stage directly. The hard clamp, the deadband mux, and the bridge-enable
  // gate all still apply; the loop is open, the safety is not.
  wire signed [13:0] clamp_pos_ol = out_clamp[13:0];
  wire signed [13:0] sp_clamped =
      (sp_active > clamp_pos_ol)  ? clamp_pos_ol :
      (sp_active < -clamp_pos_ol) ? -clamp_pos_ol : sp_active;
  wire signed [13:0] u_drive = open_loop ? sp_clamped : u14;

  wire signed [13:0] out1, out2;

  servo_output_mux mux_0 (
    .aclk(aclk), .aresetn(aresetn),
    .enable(bridge_en),          // outputs live only with the bridge closed
    .u14(u_drive), .deadband(deadband), .out2_invert(out2_invert),
    .out1(out1), .out2(out2)
  );

  assign m_axis_tdata = {2'b00, out2, 2'b00, out1};
  assign m_axis_tvalid = 1'b1;

  // ------------------------------------------------------------------
  // Boost cap (DIO2): manual bit, or auto = from the FSM's integrator-clear
  // (arming / post-flip re-enable) until |I| first reaches ~87.5% of the
  // setpoint. Gated off while the setpoint is forced to zero.
  // ------------------------------------------------------------------
  reg auto_boost;
  wire [21:0] abs_i  = i_dec[21] ? -i_dec : i_dec;
  wire signed [21:0] sp20 = {sp_active, 7'b0000000};
  wire [21:0] abs_sp = sp20[21] ? -sp20 : sp20;
  wire [21:0] boost_thresh = abs_sp - (abs_sp >> 3);

  always @(posedge aclk) begin
    if (!aresetn)
      auto_boost <= 1'b0;
    else if (fsm_int_clear)
      auto_boost <= 1'b1;
    else if (!sp_force_zero && tick_i && (abs_i >= boost_thresh))
      auto_boost <= 1'b0;
    boost_o <= aresetn & (boost_mode ? (auto_boost & ~sp_force_zero)
                                     : boost_manual);
  end

  // ------------------------------------------------------------------
  // Heartbeat: depends on nothing but the clock
  // ------------------------------------------------------------------
  wire [31:0] hb_cnt;
  servo_heartbeat #(.DIV_LOG2(HB_DIV_LOG2)) hb_0 (
    .aclk(aclk), .heartbeat(heartbeat_o), .cnt_mon(hb_cnt)
  );

  // ------------------------------------------------------------------
  // STS assembly (words 0-4; word 5 = FIFO count is added in the block
  // design; words 6-7 reserved)
  // ------------------------------------------------------------------
  assign sts_data[3:0]     = fsm_state;
  assign sts_data[4]       = fault;
  assign sts_data[5]       = bridge_en;
  assign sts_data[6]       = polarity;
  assign sts_data[7]       = armed;
  assign sts_data[8]       = out_sat;
  assign sts_data[9]       = int_railed;
  assign sts_data[10]      = sp_sign_mismatch;
  assign sts_data[11]      = timeout_hold;
  assign sts_data[31:12]   = 20'd0;
  assign sts_data[53:32]   = i_dec;          // W1, s22 (host sign-extends)
  assign sts_data[63:54]   = 10'd0;
  assign sts_data[77:64]   = sp_active;      // W2, s14 (host sign-extends)
  assign sts_data[95:78]   = 18'd0;
  assign sts_data[109:96]  = u14;            // W3, s14 (host sign-extends)
  assign sts_data[127:110] = 18'd0;
  assign sts_data[159:128] = hb_cnt;         // W4

endmodule
