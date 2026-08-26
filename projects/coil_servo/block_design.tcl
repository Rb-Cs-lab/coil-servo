# Coil current servo block design.
#
# All servo logic lives in cores/coil_servo_top.v (verified by
# sim/tb_coil_servo_top.py before this file is ever built), so this block
# design is pure plumbing, kept as close as possible to the proven
# projects/playground wiring: PS7 + clocks, axi_hub register bridge,
# fast ADC in, fast DAC out, raw-ADC capture FIFO, XADC monitoring.
#
# Register map: docs/register_map.md (CFG 512 bits, STS 256 bits at
# 0x40000000 / 0x41000000 through the hub).
#
# NOT yet built with Vivado on this machine -- first `make xpr && make bit`
# runs on the Ubuntu 24.04 host (build playground first as a smoke test).

# 125 MHz fabric clock from the ADC clock, plus the two 250 MHz DDR phases
# the DAC interface needs
cell xilinx.com:ip:clk_wiz pll_0 {
  PRIMITIVE PLL
  PRIM_IN_FREQ.VALUE_SRC USER
  PRIM_IN_FREQ 125.0
  PRIM_SOURCE Differential_clock_capable_pin
  CLKOUT1_USED true
  CLKOUT1_REQUESTED_OUT_FREQ 125.0
  CLKOUT2_USED true
  CLKOUT2_REQUESTED_OUT_FREQ 250.0
  CLKOUT2_REQUESTED_PHASE 157.5
  CLKOUT3_USED true
  CLKOUT3_REQUESTED_OUT_FREQ 250.0
  CLKOUT3_REQUESTED_PHASE 202.5
  USE_RESET false
} {
  clk_in1_p adc_clk_p_i
  clk_in1_n adc_clk_n_i
}

cell xilinx.com:ip:processing_system7 ps_0 {
  PCW_IMPORT_BOARD_PRESET cfg/red_pitaya.xml
} {
  M_AXI_GP0_ACLK pll_0/clk_out1
}

apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {
  make_external {FIXED_IO, DDR}
  Master Disable
  Slave Disable
} [get_bd_cells ps_0]

cell xilinx.com:ip:xlconstant const_0

cell xilinx.com:ip:proc_sys_reset rst_0 {} {
  ext_reset_in const_0/dout
  dcm_locked pll_0/locked
  slowest_sync_clk pll_0/clk_out1
}

# Register bridge: PS GP0 -> axi_hub at 0x40000000.
# Addr bits [27:24]: 0 = CFG, 1 = STS, 2+ = BRAM/streams.
cell pavel-demin:user:axi_hub hub_0 {
  CFG_DATA_WIDTH 512
  STS_DATA_WIDTH 256
} {
  S_AXI ps_0/M_AXI_GP0
  aclk pll_0/clk_out1
  aresetn rst_0/peripheral_aresetn
}

# Fast ADC: IN1 = measured current [13:0], IN2 = analog setpoint [29:16]
cell pavel-demin:user:axis_red_pitaya_adc adc_0 {
  ADC_DATA_WIDTH 14
} {
  aclk pll_0/clk_out1
  adc_dat_a adc_dat_a_i
  adc_dat_b adc_dat_b_i
  adc_csn adc_csn_o
}

# The servo itself (error path, decimators, PI, output mux, flip FSM,
# heartbeat, DIO conditioning -- see cores/coil_servo_top.v)
cell pavel-demin:user:coil_servo_top servo_0 {} {
  aclk pll_0/clk_out1
  aresetn rst_0/peripheral_aresetn
  S_AXIS adc_0/M_AXIS
  cfg_data hub_0/cfg_data
  flip_req_i flip_req_i
  arm_i arm_i
  fault_i fault_i
  bridge_polarity_o bridge_polarity_o
  bridge_enable_o bridge_enable_o
  boost_o boost_o
  heartbeat_o heartbeat_o
  led_o led_o
}

# Fast DAC: OUT1 = pass bank [13:0], OUT2 = active clamp [29:16]
cell pavel-demin:user:axis_red_pitaya_dac dac_0 {
  DAC_DATA_WIDTH 14
} {
  aclk pll_0/clk_out1
  ddr_clk pll_0/clk_out2
  wrt_clk pll_0/clk_out3
  locked pll_0/locked
  S_AXIS servo_0/M_AXIS
  dac_clk dac_clk_o
  dac_rst dac_rst_o
  dac_sel dac_sel_o
  dac_wrt dac_wrt_o
  dac_dat dac_dat_o
}

# Raw ADC capture FIFO for HIL diagnostics (reset from CFG via the servo)
cell pavel-demin:user:axis_fifo fifo_0 {
  S_AXIS_TDATA_WIDTH 32
  M_AXIS_TDATA_WIDTH 32
  WRITE_DEPTH 16384
} {
  S_AXIS servo_0/M01_AXIS
  M_AXIS hub_0/S00_AXIS
  aclk pll_0/clk_out1
  aresetn servo_0/fifo_resetn
}

# XADC: AIN0 coil temperature, AIN1 rail voltage (logging only)
cell pavel-demin:user:xadc_bram xadc_0 {} {
  B_BRAM hub_0/B02_BRAM
  Vp_Vn Vp_Vn
  Vaux0 Vaux0
  Vaux1 Vaux1
  Vaux8 Vaux8
  Vaux9 Vaux9
}

# STS: servo words 0-4, FIFO fill count as word 5, words 6-7 reserved
cell xilinx.com:ip:xlconstant const_1 {
  CONST_WIDTH 64
  CONST_VAL 0
}

cell xilinx.com:ip:xlconcat concat_0 {
  NUM_PORTS 3
  IN0_WIDTH 160
  IN1_WIDTH 32
  IN2_WIDTH 64
} {
  In0 servo_0/sts_data
  In1 fifo_0/read_count
  In2 const_1/dout
  dout hub_0/sts_data
}
