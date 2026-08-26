"""cocotb bench for servo_flip_fsm.

A concurrent invariant monitor runs under every test:
  * polarity never changes unless the bridge has been open for >= deadtime
  * bridge is never enabled in DISABLE/FLIP states
Directed tests walk the full flip sequence, window qualification, timeout
hold + acknowledge, graceful stop, and arm gating.
"""

import cocotb
from cocotb.triggers import RisingEdge

from tb_util import reset, signed, start_clock

# CFG values used by every test (small for sim speed)
ZERO_WIN = 41         # counts; window at Q2.20 = 41 << 7 = 5248
HOLDOFF = 3
DEADTIME = 20         # clocks
SETTLE = 50           # clocks
TIMEOUT = 300         # clocks

S_IDLE, S_RUN, S_RAMP, S_DIS, S_FLIP, S_EN, S_SETTLE, S_THOLD = range(8)

I_BIG = 200000        # well outside the window
I_SMALL = 1000        # inside the window


def state(dut):
    return int(dut.fsm_state.value)


async def setup(dut):
    cocotb.start_soon(start_clock(dut))
    dut.servo_enable.value = 0
    dut.armed.value = 0
    dut.flip_req.value = 0
    dut.flip_fault_ack.value = 0
    dut.tick.value = 0
    dut.i_meas.value = 0
    dut.zero_win.value = ZERO_WIN
    dut.zero_holdoff.value = HOLDOFF
    dut.deadtime.value = DEADTIME
    dut.settle.value = SETTLE
    dut.flip_timeout.value = TIMEOUT
    await reset(dut)
    cocotb.start_soon(invariant_monitor(dut))


async def invariant_monitor(dut):
    prev_pol = int(dut.polarity.value)
    bridge_low_for = 10 ** 9
    while True:
        await RisingEdge(dut.aclk)
        ben = int(dut.bridge_en.value)
        pol = int(dut.polarity.value)
        st = state(dut)
        if pol != prev_pol:
            assert ben == 0, "POLARITY CHANGED WITH BRIDGE ENABLED"
            assert bridge_low_for >= DEADTIME, \
                f"polarity changed only {bridge_low_for} clocks after bridge opened"
            prev_pol = pol
        bridge_low_for = 0 if ben else bridge_low_for + 1
        if st in (S_DIS, S_FLIP):
            assert ben == 0, f"bridge enabled in state {st}"


async def dtick(dut, i_meas):
    """One decimated sample: strobe tick with i_meas, then idle a few clocks."""
    dut.i_meas.value = i_meas
    dut.tick.value = 1
    await RisingEdge(dut.aclk)
    dut.tick.value = 0
    for _ in range(4):
        await RisingEdge(dut.aclk)


async def idle(dut, n):
    for _ in range(n):
        await RisingEdge(dut.aclk)


async def wait_state(dut, target, limit, why=""):
    for _ in range(limit):
        if state(dut) == target:
            return
        await RisingEdge(dut.aclk)
    raise AssertionError(f"never reached state {target} ({why}); at {state(dut)}")


async def arm_and_run(dut):
    dut.servo_enable.value = 1
    dut.armed.value = 1
    await wait_state(dut, S_RUN, 10, "arming")
    assert dut.bridge_en.value == 1
    assert dut.sp_force_zero.value == 0
    assert dut.int_hold.value == 0


@cocotb.test()
async def reset_state_is_safe(dut):
    await setup(dut)
    assert state(dut) == S_IDLE
    assert dut.bridge_en.value == 0
    assert dut.polarity.value == 0
    assert dut.sp_force_zero.value == 1
    assert dut.int_hold.value == 1


@cocotb.test()
async def full_flip_sequence(dut):
    await setup(dut)
    await arm_and_run(dut)

    # current flowing, several ticks: nothing happens in RUN
    for _ in range(3):
        await dtick(dut, I_BIG)
    assert state(dut) == S_RUN

    # flip request -> RAMP_DOWN, setpoint forced to zero, bridge still closed
    dut.flip_req.value = 1
    await RisingEdge(dut.aclk)
    dut.flip_req.value = 0
    await wait_state(dut, S_RAMP, 5, "flip request")
    assert dut.sp_force_zero.value == 1
    assert dut.bridge_en.value == 1
    assert dut.int_hold.value == 0, "loop must keep servoing during ramp-down"

    # window qualification: 2 in-window ticks, then a noise spike -> counter
    # must reset and the FSM must NOT open the bridge
    await dtick(dut, I_SMALL)
    await dtick(dut, -I_SMALL)
    await dtick(dut, I_BIG)
    assert state(dut) == S_RAMP, "noise spike must reset the qualification"

    # 3 consecutive in-window ticks -> DISABLE
    await dtick(dut, I_SMALL)
    await dtick(dut, I_SMALL)
    await dtick(dut, -I_SMALL)
    assert state(dut) == S_DIS
    assert dut.bridge_en.value == 0
    assert dut.int_hold.value == 1

    pol_before = int(dut.polarity.value)
    await wait_state(dut, S_FLIP, DEADTIME + 5, "dead time")
    assert int(dut.polarity.value) == 1 - pol_before, "polarity must toggle"
    await wait_state(dut, S_SETTLE, DEADTIME + 10, "second dead time")
    assert dut.bridge_en.value == 1
    assert dut.int_hold.value == 0
    assert dut.sp_force_zero.value == 1, "setpoint still forced during settle"
    await wait_state(dut, S_RUN, SETTLE + 10, "settle delay")
    assert dut.sp_force_zero.value == 0


@cocotb.test()
async def timeout_parks_in_hold_and_never_flips(dut):
    await setup(dut)
    await arm_and_run(dut)
    pol_before = int(dut.polarity.value)

    dut.flip_req.value = 1
    await RisingEdge(dut.aclk)
    dut.flip_req.value = 0
    await wait_state(dut, S_RAMP, 5)

    # current never reaches the window; ticks keep coming
    for _ in range(TIMEOUT // 5 + 10):
        await dtick(dut, I_BIG)
        if state(dut) == S_THOLD:
            break
    assert state(dut) == S_THOLD, "timeout must park in TIMEOUT_HOLD"
    assert dut.bridge_en.value == 1, "hold keeps the bridge closed"
    assert int(dut.polarity.value) == pol_before, "must never flip at current"
    assert dut.sp_force_zero.value == 1, "hold keeps pulling toward zero"

    # host acknowledge -> back to RUN
    dut.flip_fault_ack.value = 1
    await wait_state(dut, S_RUN, 5, "acknowledge")
    dut.flip_fault_ack.value = 0
    assert dut.sp_force_zero.value == 0


@cocotb.test()
async def graceful_stop_on_servo_disable(dut):
    await setup(dut)
    await arm_and_run(dut)
    pol_before = int(dut.polarity.value)

    dut.servo_enable.value = 0
    await wait_state(dut, S_RAMP, 5, "graceful stop entry")
    assert dut.bridge_en.value == 1, "bridge must NOT drop at current"
    assert dut.sp_force_zero.value == 1

    for _ in range(HOLDOFF):
        await dtick(dut, I_SMALL)
    assert state(dut) == S_DIS
    await wait_state(dut, S_IDLE, DEADTIME + 5, "stop completes")
    assert dut.bridge_en.value == 0
    assert int(dut.polarity.value) == pol_before, "stop must not flip"


@cocotb.test()
async def arm_gating(dut):
    await setup(dut)

    # enabled but not armed: stays IDLE, bridge off
    dut.servo_enable.value = 1
    await idle(dut, 10)
    assert state(dut) == S_IDLE and dut.bridge_en.value == 0

    dut.armed.value = 1
    await wait_state(dut, S_RUN, 5)

    # disarm mid-run: bridge stays (decided semantics), but flips are ignored
    dut.armed.value = 0
    await idle(dut, 5)
    assert state(dut) == S_RUN and dut.bridge_en.value == 1
    dut.flip_req.value = 1
    await RisingEdge(dut.aclk)
    dut.flip_req.value = 0
    await idle(dut, 5)
    assert state(dut) == S_RUN, "flip while disarmed must be ignored"


@cocotb.test()
async def flip_request_ignored_outside_run(dut):
    await setup(dut)
    dut.flip_req.value = 1
    await RisingEdge(dut.aclk)
    dut.flip_req.value = 0
    await idle(dut, 10)
    assert state(dut) == S_IDLE
    assert dut.bridge_en.value == 0
