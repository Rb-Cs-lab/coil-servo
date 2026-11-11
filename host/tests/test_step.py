"""step tool: what it writes to the setpoint register (no board needed)."""

import pytest

from coil_servo_host.config import load_channel
from coil_servo_host.step import setpoint_counts


def test_closed_loop_amps_are_sensor_counts():
    ch = load_channel("mot_bench")
    sp, _ = setpoint_counts(ch, 2.0, None, open_loop=False)
    assert sp == round(2.0 / 200.0 * 8192)


def test_open_loop_amps_go_through_the_model():
    ch = load_channel("mot_bench")
    sp, _ = setpoint_counts(ch, 1.0, None, open_loop=True)
    assert sp == round(ch["model"].out1_for_current(1.0) * 8192)
    assert setpoint_counts(ch, 0.0, None, open_loop=True)[0] == 0


def test_open_loop_counts_bypass_the_model():
    ch = load_channel("mot_bench")
    assert setpoint_counts(ch, None, 3600, open_loop=True)[0] == 3600


@pytest.mark.parametrize("amps,counts,open_loop", [
    (None, 3600, False),        # counts are an OUT1 command: open loop only
    (None, 4001, True),         # above the bench clamp (4000)
    (None, -1, True),
    (2.6, None, False),         # above the bench rated 2.5 A
    (-1.0, None, True),         # OUT1 only drives up
])
def test_mistakes_are_refused(amps, counts, open_loop):
    with pytest.raises(SystemExit):
        setpoint_counts(load_channel("mot_bench"), amps, counts, open_loop)
