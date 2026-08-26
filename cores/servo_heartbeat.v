`timescale 1 ns / 1 ps

// Watchdog heartbeat (safety invariant 5): DIO6 toggles at
// 125 MHz / 2^(DIV_LOG2+1) ~ 954 Hz for DIV_LOG2 = 16, unconditionally --
// no reset input on purpose. It depends on nothing but the fabric clock, so
// an external monostable can drop bridge enable if the clock (or the whole
// design) stops. The counter has a power-on initial value (supported by
// Xilinx 7-series and by the simulators), not a reset.

module servo_heartbeat #
(
  parameter integer DIV_LOG2 = 16
)
(
  input  wire aclk,
  output wire heartbeat
);

  reg [DIV_LOG2:0] cnt = 0;

  always @(posedge aclk) cnt <= cnt + 1'b1;

  assign heartbeat = cnt[DIV_LOG2];

endmodule
