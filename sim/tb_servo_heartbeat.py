"""cocotb bench for servo_heartbeat (built with DIV_LOG2=6 for sim speed:
toggle every 64 clocks, period 128)."""

import cocotb
from cocotb.triggers import RisingEdge, Timer

PERIOD = 128  # 2^(6+1) clocks


async def clock(dut):
    while True:
        dut.aclk.value = 0
        await Timer(4, "ns")
        dut.aclk.value = 1
        await Timer(4, "ns")


@cocotb.test()
async def toggles_with_exact_period(dut):
    cocotb.start_soon(clock(dut))
    # let the counter run past any initial edge
    for _ in range(PERIOD):
        await RisingEdge(dut.aclk)

    edges = []
    prev = int(dut.heartbeat.value)
    for n in range(PERIOD * 5):
        await RisingEdge(dut.aclk)
        cur = int(dut.heartbeat.value)
        if cur != prev:
            edges.append(n)
            prev = cur

    assert len(edges) >= 8, "heartbeat is not toggling"
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    assert all(g == PERIOD // 2 for g in gaps), f"irregular heartbeat: {gaps}"
