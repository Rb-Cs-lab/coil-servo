"""cocotb bench for servo_flip_fsm.

A concurrent invariant monitor runs under every test:
  * polarity never changes unless the bridge has been open for >= deadtime
  * bridge is never enabled in DISABLE/FLIP states
  * in TRIP/TRIPPED the DAC outputs are killed, the bridge never re-closes,
    and in TRIPPED it is open
  * the bridge never closes without a preceding arm edge (IDLE -> RUN)
Directed tests walk the full flip sequence, window qualification, timeout
hold + acknowledge, graceful stop, arm gating (edge, not level), and the
over-current trip (persistence, freewheel-then-open, timeout, latch, ack).
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
TRIP_LEVEL = 4096     # counts; Q2.20 threshold = 4096 << 7 = 524288
TRIP_TICKS = 3
TRIP_TIMEOUT = 400    # clocks

(S_IDLE, S_RUN, S_RAMP, S_DIS, S_FLIP, S_EN, S_SETTLE, S_THOLD,
 S_TRIP, S_TRIPPED) = range(10)

I_BIG = 200000        # well outside the window, below the trip
I_SMALL = 1000        # inside the window
I_TRIP = 600000       # above the trip level


def state(dut):
    return int(dut.fsm_state.value)


async def setup(dut, armed_at_load=0, trip_timeout=TRIP_TIMEOUT):
    cocotb.start_soon(start_clock(dut))
    dut.servo_enable.value = 0
    dut.armed.value = armed_at_load
    dut.flip_req.value = 0
    dut.flip_fault_ack.value = 0
    dut.trip_ack.value = 0
    dut.trip_level.value = TRIP_LEVEL
    dut.trip_ticks.value = TRIP_TICKS
    dut.trip_timeout.value = trip_timeout
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
    prev_ben = int(dut.bridge_en.value)
    prev_st = state(dut)
    prev_arm = int(dut.armed.value)
    arm_edge_seen = False
    bridge_low_for = 10 ** 9
    while True:
        await RisingEdge(dut.aclk)
        ben = int(dut.bridge_en.value)
        pol = int(dut.polarity.value)
        st = state(dut)
        arm = int(dut.armed.value)
        if st in (S_TRIP, S_TRIPPED):
            assert int(dut.out_kill.value) == 1, "DACs not killed in a trip state"
            assert not (ben and not prev_ben), "bridge re-closed during a trip"
        if st == S_TRIPPED:
            assert ben == 0, "bridge closed in TRIPPED"
        if prev_st == S_IDLE and st == S_RUN:
            assert arm_edge_seen, "left IDLE without an arm edge"
        if st == S_IDLE and prev_st != S_IDLE:
            arm_edge_seen = False        # a fresh edge is needed again
        if arm and not prev_arm:
            arm_edge_seen = True
        prev_arm, prev_ben, prev_st = arm, ben, st
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
async def arm_level_high_at_load_is_not_an_edge(dut):
    """Safety invariant 2 (MVP): arm already high when the bitstream loads
    must not close the bridge; the control computer has to re-raise it."""
    await setup(dut, armed_at_load=1)
    dut.servo_enable.value = 1
    await idle(dut, 20)
    assert state(dut) == S_IDLE and dut.bridge_en.value == 0
    dut.armed.value = 0
    await idle(dut, 3)
    dut.armed.value = 1
    await wait_state(dut, S_RUN, 5, "fresh arm edge")


@cocotb.test()
async def arm_edge_before_enable_does_not_count(dut):
    await setup(dut)
    dut.armed.value = 1                  # edge while servo_enable = 0
    await idle(dut, 5)
    dut.servo_enable.value = 1
    await idle(dut, 20)
    assert state(dut) == S_IDLE and dut.bridge_en.value == 0


@cocotb.test()
async def rearm_needed_after_graceful_stop(dut):
    await setup(dut)
    await arm_and_run(dut)
    dut.servo_enable.value = 0
    await wait_state(dut, S_RAMP, 5)
    for _ in range(HOLDOFF):
        await dtick(dut, I_SMALL)
    await wait_state(dut, S_IDLE, DEADTIME + 5)
    dut.servo_enable.value = 1           # arm still high: no edge
    await idle(dut, 20)
    assert state(dut) == S_IDLE and dut.bridge_en.value == 0
    dut.armed.value = 0
    await idle(dut, 2)
    dut.armed.value = 1
    await wait_state(dut, S_RUN, 5, "re-arm")


async def trip_from_run(dut):
    """Arm, run, then over-current for TRIP_TICKS samples -> TRIP."""
    await arm_and_run(dut)
    for _ in range(TRIP_TICKS - 1):
        await dtick(dut, I_TRIP)
    assert state(dut) == S_RUN, "tripped before trip_ticks samples"
    await dtick(dut, I_TRIP)
    assert state(dut) == S_TRIP
    assert dut.out_kill.value == 1
    assert dut.bridge_en.value == 1, "bridge must stay closed: freewheel first"
    assert dut.sp_force_zero.value == 1
    assert dut.int_hold.value == 1


@cocotb.test()
async def trip_freewheels_then_opens_and_latches(dut):
    await setup(dut)
    pol = int(dut.polarity.value)
    await trip_from_run(dut)

    # current decaying but still above the window: bridge stays closed;
    # servo_enable and flip requests are ignored while tripped
    dut.servo_enable.value = 0
    dut.flip_req.value = 1
    await RisingEdge(dut.aclk)
    dut.flip_req.value = 0
    for _ in range(3):
        await dtick(dut, I_BIG)
    assert state(dut) == S_TRIP and dut.bridge_en.value == 1

    for _ in range(HOLDOFF):
        await dtick(dut, I_SMALL)
    assert state(dut) == S_TRIPPED
    assert dut.bridge_en.value == 0
    assert int(dut.polarity.value) == pol, "a trip must never flip"

    # latched: stays put until acknowledged, even with enable + arm
    dut.servo_enable.value = 1
    await idle(dut, 50)
    assert state(dut) == S_TRIPPED
    dut.trip_ack.value = 1
    await wait_state(dut, S_IDLE, 5, "trip_ack")
    dut.trip_ack.value = 0
    await idle(dut, 20)
    assert state(dut) == S_IDLE, "arm still high: needs a fresh edge"
    assert dut.out_kill.value == 0
    dut.armed.value = 0
    await idle(dut, 2)
    dut.armed.value = 1
    await wait_state(dut, S_RUN, 5, "re-arm after trip")
    assert dut.out_kill.value == 0 and dut.bridge_en.value == 1


@cocotb.test()
async def trip_needs_consecutive_samples(dut):
    await setup(dut)
    await arm_and_run(dut)
    for _ in range(5):
        for _ in range(TRIP_TICKS - 1):
            await dtick(dut, I_TRIP)
        await dtick(dut, I_BIG)          # one sample below resets the count
    assert state(dut) == S_RUN
    await dtick(dut, -I_TRIP)            # magnitude counts, either sign
    await dtick(dut, -I_TRIP)
    await dtick(dut, I_TRIP)
    assert state(dut) == S_TRIP


@cocotb.test()
async def trip_timeout_opens_the_bridge_anyway(dut):
    """Current that does not decay (e.g. a shorted pass FET): the bridge
    opens after trip_timeout regardless."""
    await setup(dut)
    await trip_from_run(dut)
    for _ in range(TRIP_TIMEOUT // 5 + 10):
        await dtick(dut, I_TRIP)
        if state(dut) == S_TRIPPED:
            break
    assert state(dut) == S_TRIPPED and dut.bridge_en.value == 0


@cocotb.test()
async def trip_timeout_zero_opens_at_once(dut):
    """trip_timeout = 0: the bridge opens on the clock after the trip (the
    coil energy then goes into the supply -- an explicit choice)."""
    await setup(dut, trip_timeout=0)
    await arm_and_run(dut)
    for _ in range(TRIP_TICKS - 1):
        await dtick(dut, I_TRIP)
    dut.i_meas.value = I_TRIP
    dut.tick.value = 1
    await RisingEdge(dut.aclk)
    dut.tick.value = 0
    await RisingEdge(dut.aclk)
    assert state(dut) == S_TRIP and dut.out_kill.value == 1
    await RisingEdge(dut.aclk)
    await RisingEdge(dut.aclk)
    assert state(dut) == S_TRIPPED and dut.bridge_en.value == 0


@cocotb.test()
async def trip_during_a_flip_never_flips(dut):
    await setup(dut)
    await arm_and_run(dut)
    pol = int(dut.polarity.value)
    dut.flip_req.value = 1
    await RisingEdge(dut.aclk)
    dut.flip_req.value = 0
    await wait_state(dut, S_RAMP, 5)
    for _ in range(TRIP_TICKS):
        await dtick(dut, I_TRIP)
    assert state(dut) == S_TRIP
    for _ in range(HOLDOFF):
        await dtick(dut, I_SMALL)
    await idle(dut, 3 * DEADTIME)
    assert state(dut) == S_TRIPPED
    assert int(dut.polarity.value) == pol


@cocotb.test()
async def no_trip_in_idle(dut):
    await setup(dut)
    for _ in range(2 * TRIP_TICKS):
        await dtick(dut, I_TRIP)
    assert state(dut) == S_IDLE


@cocotb.test()
async def flip_request_ignored_outside_run(dut):
    await setup(dut)
    dut.flip_req.value = 1
    await RisingEdge(dut.aclk)
    dut.flip_req.value = 0
    await idle(dut, 10)
    assert state(dut) == S_IDLE
    assert dut.bridge_en.value == 0
