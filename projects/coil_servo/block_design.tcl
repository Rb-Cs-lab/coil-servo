# Coil current servo — block design STUB (session 1).
#
# This is the infrastructure skeleton only: PS7, clocks, axi_hub register
# bridge, fast ADC in, fast DAC out pinned to ZERO, XADC monitoring.
# The servo cores (PI, decimator, output mux, flip FSM, safety, heartbeat)
# are added in later sessions after they pass their cocotb benches.
# Wiring follows projects/playground (proven upstream at tag 20251012).
# NOT yet built with Vivado — first `make xpr && make bit` run happens on the
# Ubuntu 24.04 host in session 6.
#
# CFG/STS field layout here is provisional bring-up plumbing, not the real
# register map; the real map is defined in docs/register_map.md (session 2+).

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
  CFG_DATA_WIDTH 64
  STS_DATA_WIDTH 64
} {
  S_AXI ps_0/M_AXI_GP0
  aclk pll_0/clk_out1
  aresetn rst_0/peripheral_aresetn
}

# CFG bit 0: ADC capture FIFO reset (bring-up plumbing)
cell pavel-demin:user:port_slicer slice_0 {
  DIN_WIDTH 64 DIN_FROM 0 DIN_TO 0
} {
  din hub_0/cfg_data
}

# CFG bits [15:8]: LEDs (bring-up blinker / register-write sanity check)
cell pavel-demin:user:port_slicer slice_1 {
  DIN_WIDTH 64 DIN_FROM 15 DIN_TO 8
} {
  din hub_0/cfg_data
  dout led_o
}

# Fast ADC: IN1 = measured current, IN2 = analog setpoint (channels packed
# into one 32-bit stream word, 16 bits each)
cell pavel-demin:user:axis_red_pitaya_adc adc_0 {
  ADC_DATA_WIDTH 14
} {
  aclk pll_0/clk_out1
  adc_dat_a adc_dat_a_i
  adc_dat_b adc_dat_b_i
  adc_csn adc_csn_o
}

# Raw ADC capture into the hub for HIL diagnostics
cell pavel-demin:user:axis_fifo fifo_0 {
  S_AXIS_TDATA_WIDTH 32
  M_AXIS_TDATA_WIDTH 32
  WRITE_DEPTH 16384
} {
  S_AXIS adc_0/M_AXIS
  M_AXIS hub_0/S00_AXIS
  aclk pll_0/clk_out1
  aresetn slice_0/dout
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

# Fast DAC pinned to zero: OUT1 (pass bank) and OUT2 (clamp) both command
# zero current until the servo cores replace this constant (safety
# invariant: reset/idle state is off).
cell xilinx.com:ip:xlconstant const_1 {
  CONST_WIDTH 32
  CONST_VAL 0
}

cell pavel-demin:user:axis_constant zero_0 {
  AXIS_TDATA_WIDTH 32
} {
  cfg_data const_1/dout
  aclk pll_0/clk_out1
}

cell pavel-demin:user:axis_red_pitaya_dac dac_0 {
  DAC_DATA_WIDTH 14
} {
  aclk pll_0/clk_out1
  ddr_clk pll_0/clk_out2
  wrt_clk pll_0/clk_out3
  locked pll_0/locked
  S_AXIS zero_0/M_AXIS
  dac_clk dac_clk_o
  dac_rst dac_rst_o
  dac_sel dac_sel_o
  dac_wrt dac_wrt_o
  dac_dat dac_dat_o
}

# STS: FIFO fill count in the low word, zero in the high word
cell xilinx.com:ip:xlconstant const_2 {
  CONST_WIDTH 32
  CONST_VAL 0
}

cell xilinx.com:ip:xlconcat concat_0 {
  NUM_PORTS 2
  IN0_WIDTH 32
  IN1_WIDTH 32
} {
  In0 fifo_0/read_count
  In1 const_2/dout
  dout hub_0/sts_data
}

# E1 DIO (exp_p_tri_io / exp_n_tri_io) intentionally unconnected in this
# stub: pins stay high-impedance, which is the safe state (bridge enable
# low = all FETs off). The flip FSM / safety cores drive them later.
