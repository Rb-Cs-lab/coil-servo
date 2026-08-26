"""Shared helpers for the cocotb testbenches."""

from cocotb.triggers import RisingEdge, Timer


def signed(handle) -> int:
    """Read a DUT signal as a signed integer (cocotb 1.x / 2.x compatible)."""
    v = handle.value
    try:
        return v.to_signed()            # cocotb 2.x LogicArray
    except AttributeError:
        return v.signed_integer         # cocotb 1.x BinaryValue


async def start_clock(dut, period_ns: int = 8):
    """8 ns clock (125 MHz), hand-rolled to avoid Clock API drift."""
    while True:
        dut.aclk.value = 0
        await Timer(period_ns // 2, "ns")
        dut.aclk.value = 1
        await Timer(period_ns // 2, "ns")


async def reset(dut, cycles: int = 4):
    dut.aresetn.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.aclk)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)
