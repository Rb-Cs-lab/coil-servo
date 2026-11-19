"""Build the Ansys Electronics Desktop project of the coil-servo power stage.

    ansys\\.venv\\Scripts\\python.exe ansys\\build_project.py            # both designs
    ansys\\.venv\\Scripts\\python.exe ansys\\build_project.py bench      # one design

Writes ansys/coil_servo.aedt (open it with Ansys Electronics Desktop
Student 2025 R2) with one "Circuit" design per configuration:

    bench  the power stage exactly as it is wired on the bench now
           (3 A bench supply, 45 uH test coil, 3.2 ohm + 10 uF damper)
    mot    the same electronics on the SE-600-5 with the two MOT coils
           (planned Test 4 wiring -- NOT built yet, values provisional)

A second project file, ansys/coil_servo_fast_turnoff.aedt, holds the same two
circuits with the planned fast turn-off parts added (NOT fitted yet):

    bench_ft, mot_ft   + Littelfuse 5KP30A between rail bar and return bar
                       + IXYS DSS2X101-015A blocking diode in the supply + lead

    ansys\\.venv\\Scripts\\python.exe ansys\\build_project.py bench_ft mot_ft

(only the project files of the designs you name are rebuilt).

The circuit is described once, in PARTS below, as a table of
(group, name, kind, pins -> net names, values): that table is the electrical
truth. layout.py says where each part sits and how the wires are drawn; the
builder then checks that every pin ended up on the net the table asks for.
The part models are in models/coil_servo_parts.lib.
See README.md in this folder.
"""
from __future__ import annotations

import math
import os
import sys

import layout

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "models", "coil_servo_parts.lib")
# project file -> the designs in it
PROJECTS = {
    "coil_servo.aedt": ["bench", "mot"],                          # as built / as planned for Test 4
    "coil_servo_fast_turnoff.aedt": ["bench_ft", "mot_ft"],       # + 5KP30A and DSS2X101-015A (not fitted yet)
}
PROJECT = os.path.join(HERE, "coil_servo.aedt")


def project_of(design: str) -> str:
    """Full path of the project file a design lives in."""
    for fname, designs in PROJECTS.items():
        if design in designs:
            return os.path.join(HERE, fname)
    raise KeyError(design)

AEDT_VERSION = "2025.2"

G = 0.00254  # schematic grid, 100 mil

# Measured 3 mA thresholds of the six installed pass FETs (#1,2,4,7,9,10)
VTH_3MA = [3.52, 3.53, 3.52, 3.56, 3.50, 3.52]
VTH_MEAN = sum(VTH_3MA) / len(VTH_3MA)

# ---------------------------------------------------------------------------
# Design variables. Everything a test changes is a variable, so a scenario is
# only a set of values (run_sims.py), and you can edit them in the Ansys GUI
# (Circuit > Design Properties).
# ---------------------------------------------------------------------------
COMMON_VARS = {
    # --- controller (Red Pitaya block) ---
    "kp": ("31.2", "PI proportional gain, OUT1 counts per amp (bench, tuned 11-12)"),
    "ki": ("942e3", "PI integral gain, counts per (amp second) = 942 counts/(A ms)"),
    "u_clamp": ("4100", "OUT1 command clamp, counts"),
    "u_floor": ("3450", "RUN floor, counts"),
    "trip_amps": ("2.8", "firmware over-current trip level, amps"),
    "vt_pass": ("3.892", "pass-FET fitted threshold, volts: 3.892 cold, 3.868 warm"),
    "V_opamp": ("12.01V", "OPA994 supply rail (measured 11-13; 5.18 V before)"),
    # --- test stimulus: what the firmware / operator does ---
    "sp_amps": ("2", "setpoint, amps (closed loop)"),
    "slew_up": ("200e3", "setpoint ramp, amps per second (firmware: 200 A/ms)"),
    "t_sp_on": ("100us", "time the setpoint is applied"),
    "t_sp_len": ("1s", "how long the setpoint stays (then it steps to zero)"),
    "ol_kcounts": ("0", "open-loop OUT1 command in thousands of counts; 0 = closed loop"),
    "t_run_off": ("1s", "time OUT1 is forced to zero (stop / snap / open-loop turn-off)"),
    "t_en_off": ("1s", "time the bridge-enable line DIO1 drops (bridge opened)"),
    "t_hb_off": ("1s", "time the heartbeat wire is pulled"),
    "t_in1_off": ("1s", "time the IN1 cable loses contact"),
    # --- extra knobs used by some tests (defaults = not used) ---
    "sp_fall": ("1us", "how fast the setpoint falls at the end of t_sp_len (firmware: 50 A/ms toward a non-zero target)"),
    "sp2_amps": ("0", "second setpoint step, ADDED to the first one at t_sp2_on (negative = step down or sign change)"),
    "t_sp2_on": ("1s", "time of the second setpoint step"),
    "slew2": ("200e3", "ramp of the second setpoint step, amps per second"),
    "snap_amps": ("0.1", "zero-setpoint snap threshold, amps; -1 = snap off (firmware before rev 5)"),
    "ol_step": ("0", "open-loop step added to ol_kcounts at t_ol_step, thousands of counts"),
    "t_ol_step": ("1s", "time of the open-loop step"),
    "t_ol_ramp": ("10ns", "duration of the open-loop step (long = slow sweep)"),
    "t_run_len": ("10s", "how long OUT1 stays forced to zero after t_run_off"),
    "t_pol": ("1s", "time the polarity line DIO0 goes low (reverse direction); 0 = reverse from the start"),
    "en_gap": ("20s", "the bridge-enable line comes back this long after t_en_off (the flip dead time)"),
    "t_hb_len": ("10s", "how long the heartbeat wire stays pulled"),
    "t_rst_per": ("20s", "the reset button is pressed again this long after the first press"),
    "R_term": ("50ohm", "terminator at the op-amp input (1e9 = not fitted)"),
    "k_pass": ("3.656", "pass-FET current law steepness, A/V^2 per FET: 3.656 = fitted on the bench below 2.5 A; the datasheet curve near 17 A per FET is several times steeper (10-30)"),
    "cgd_pass": ("1e-9", "pass-FET drain-gate capacitance, farads. 1 nF = datasheet value at 25 V; it is several times larger at the ~5 V the FETs work at"),
}

CONFIGS = {
    "bench": {
        "vars": {
            "V_sup": ("5.06", "bench supply open-circuit voltage (measured)"),
            "I_lim": ("3", "bench supply current limit, amps"),
            "C_sup": ("100e-6", "supply output capacitance, farads (INFERRED from the surge trace)"),
            "R_reg": ("20mOhm", "how much the supply voltage sags per amp (ASSUMED)"),
            "R_lead": ("0.2ohm", "supply leads, both ways (measured 0.17-0.24 ohm)"),
            "L_lead": ("3.6uH", "supply leads (fitted to the supply ripple)"),
            "R_lead_ac": ("20ohm", "AC loss across the lead inductance (ASSUMED: tuned so lead ringing dies in ~10 us)"),
            "L_coil": ("37uH", "test coil, 12 turns. From the 11-12 switch-off trace (0.52 V across the coil, 13.3 mA/us); the free-air calculation in channels.py gives 45 uH"),
            "R_coil": ("25mOhm", "test coil (CALCULATED)"),
            "R_damp1": ("1ohm", "damper resistor 1 (fitted 11-12)"),
            "R_damp2": ("2.2ohm", "damper resistor 2"),
            "C_damp": ("10uF", "damper capacitor"),
        },
    },
    "mot": {
        "vars": {
            "V_sup": ("5.0", "SE-600-5"),
            "I_lim": ("110", "SE-600-5 current limit, amps (ASSUMED)"),
            "C_sup": ("10e-3", "SE-600-5 output capacitance, farads (ASSUMED, unknown)"),
            "R_reg": ("0.5mOhm", "SE-600-5 sag per amp: 1 % load regulation = 50 mV at 100 A (ASSUMED from the datasheet figure)"),
            "R_lead": ("1.5mOhm", "4/0 supply cabling (ASSUMED)"),
            "L_lead": ("1uH", "supply cabling loop (ASSUMED)"),
            "R_lead_ac": ("20ohm", "AC loss across the lead inductance (ASSUMED)"),
            "L_coil": ("8uH", "ONE MOT coil (pair 16 uH, Radia-computed)"),
            "R_coil": ("3.7mOhm", "ONE MOT coil incl. its share of the coil cable (pair 6.4 + 1.0 mOhm, PROVISIONAL)"),
            "R_damp1": ("1.6ohm", "PROPOSED MOT damper resistor (not built)"),
            "R_damp2": ("1mOhm", "(single resistor in the MOT proposal)"),
            "C_damp": ("22uF", "PROPOSED MOT damper capacitor (not built)"),
            # controller starting point for the 100 A plant (CLAUDE.md, Q7)
            "kp": ("3.7", "PROVISIONAL starting gain for the MOT plant"),
            "ki": ("111e3", "PROVISIONAL"),
            "u_clamp": ("5200", "PROVISIONAL: must be set from measured current in Test 4"),
            "u_floor": ("3450", "as bench"),
            "trip_amps": ("100", "firmware trip = supply rating"),
            "sp_amps": ("50", "setpoint, amps"),
        },
    },
}


# The fast turn-off designs use the same values as the designs they are built on.
for _c in ("bench", "mot"):
    CONFIGS[_c + "_ft"] = {"vars": dict(CONFIGS[_c]["vars"])}
    CONFIGS[_c + "_ft"]["vars"].update({
        "C_rail": ("5e-9", "capacitance between rail bar and return bar, farads: the 5KP30A itself (5 nF ASSUMED); larger = a capacitor fitted there"),
        "R_bleed": ("200kOhm", "resistance across the blocking diode: 200 kOhm = its own leakage (ASSUMED); 1 kOhm = a bleeder resistor fitted across it"),
    })
del CONFIGS["mot_ft"]["vars"]["R_damp2"]      # that design has one damper resistor, see parts()


def parts(config: str) -> list[tuple]:
    """The circuit as a table: (group, name, kind, {pin: net}, {property: value})."""
    P: list[tuple] = []
    ft, config = layout.fast(config), layout.base(config)
    lead_end = "rail_in" if ft else "rail"      # with the blocking diode the lead no longer ends on the rail bar

    # ---- supply and leads ------------------------------------------------
    grp = "1 SUPPLY: bench supply 5 V / 3 A + leads" if config == "bench" else "1 SUPPLY: Mean Well SE-600-5 + 4/0 cable"
    P += [
        (grp, "PSU", "DC_SUPPLY", {"p": "sup_p", "n": "0"}, {"VSET": "V_sup", "ILIM": "I_lim", "COUT": "C_sup", "RREG": "R_reg"}),
        (grp, "R_lead", "R", {"1": "sup_p", "2": "lead_m"}, {"R": "R_lead"}),
        (grp, "L_lead", "L", {"1": "lead_m", "2": lead_end}, {"L": "L_lead"}),
        # not a part: high-frequency loss of the leads (skin effect), so lead ringing dies in ~10 us as on the bench
        (grp, "R_lead_ac", "R", {"1": "lead_m", "2": lead_end}, {"R": "R_lead_ac"}),
        (grp, "V_logic5", "VDC", {"positive": "v5", "negative": "0"}, {"DC": "5V"}),
    ]

    if ft:
        grp = "1b FAST TURN-OFF PARTS (planned, not fitted): DSS2X101-015A blocking diode, 5KP30A across the bridge input"
        P += [
            # both halves of the dual Schottky in parallel, supply side -> rail bar
            (grp, "D_block", "SCHOTTKY_DSS2X101", {"a": "rail_in", "k": "rail"}, {"RLEAK": "R_bleed"}),
            # cathode on the rail bar, anode on the return bar
            (grp, "D_clamp", "TVS_5KP30A", {"k": "rail", "a": "ret"}, {"CJ": "C_rail"}),
        ]

    # ---- H-bridge: 4 x IXFN420N10T, each with UCC5350 + RO-0515S ---------
    grp = "2 H-BRIDGE: 4 x IXFN420N10T, UCC5350SB drivers, RO-0515S supplies"
    # One block per switch position (MOSFET + driver + floating supply + gate
    # resistors, see BRIDGE_SWITCH in the model file): the Student licence
    # allows only 50 components per design.
    for k, (d, s, inp) in enumerate([("rail", "A", "pol"), ("rail", "B", "npol"), ("A", "ret", "npol"), ("B", "ret", "pol")], 1):
        P += [(grp, f"Q{k}", "BRIDGE_SWITCH", {"d": d, "g": f"g{k}", "s": s, "inp": inp, "inn": "dis", "v5": "v5"}, {})]

    # ---- coil(s), damper, varistor(s), current sensor --------------------
    # The LEM is wired "backwards" and the AD8421 inputs are swapped (as built):
    # the two sign changes cancel, IN1 is positive for forward current.
    if config == "bench":
        grp = "3 COIL: 45 uH test coil, RC damper, S20K20 varistor, LEM LF 310-S"
        P += [
            (grp, "R_damp1", "R", {"1": "A", "2": "dmp1"}, {"R": "R_damp1"}),
            (grp, "R_damp2", "R", {"1": "dmp1", "2": "dmp2"}, {"R": "R_damp2"}),
            (grp, "C_damp", "C", {"1": "dmp2", "2": "B"}, {"C": "C_damp"}),
            (grp, "RV1", "VARISTOR_S20K20", {"a": "A", "b": "B"}, {}),
            (grp, "LEM", "LEM_LF310S", {"ip": "coil_a", "in": "A", "m": "lem_m"}, {}),
            (grp, "L_coil", "L", {"1": "coil_a", "2": "coil_m"}, {"L": "L_coil"}),
            (grp, "R_coil", "R", {"1": "coil_m", "2": "B"}, {"R": "R_coil"}),
        ]
    else:
        grp = "3 COILS: MOT pair, varistor per coil, LEM between the coils, PROPOSED damper"
        P += [
            (grp, "R_damp1", "R", {"1": "A", "2": "dmp1"}, {"R": "R_damp1"}),
            (grp, "R_damp2", "R", {"1": "dmp1", "2": "dmp2"}, {"R": "R_damp2"}),
            (grp, "C_damp", "C", {"1": "dmp2", "2": "B"}, {"C": "C_damp"}),
            (grp, "RV1", "VARISTOR_S20K20", {"a": "A", "b": "mid_a"}, {}),
            (grp, "L_coil1", "L", {"1": "A", "2": "c1"}, {"L": "L_coil"}),
            (grp, "R_coil1", "R", {"1": "c1", "2": "mid_a"}, {"R": "R_coil"}),
            (grp, "LEM", "LEM_LF310S", {"ip": "coil_a", "in": "mid_a", "m": "lem_m"}, {}),
            (grp, "L_coil", "L", {"1": "coil_a", "2": "coil_m"}, {"L": "L_coil"}),
            (grp, "R_coil", "R", {"1": "coil_m", "2": "B"}, {"R": "R_coil"}),
            (grp, "RV2", "VARISTOR_S20K20", {"a": "coil_a", "b": "B"}, {}),
        ]
        if ft:
            # The Student licence allows 50 components and mot + 2 would be 51: the proposed
            # MOT damper is ONE resistor, so its 1 mOhm placeholder twin is left out here.
            P = [p for p in P if p[1] != "R_damp2"]
            P = [(g, n, k, {"1": "A", "2": "dmp2"}, v) if n == "R_damp1" else (g, n, k, pins, v) for g, n, k, pins, v in P]

    # ---- pass bank ------------------------------------------------------
    grp = "4 PASS BANK: 6 x IXTN200N10L2, 24.9 ohm gate, 47 k pull-down, MP930 0.03 ohm ballast, 5KP12CA"
    # One block per leg (MOSFET + gate resistor + pull-down + ballast, PASS_LEG).
    for k, vth in enumerate(VTH_3MA, 1):
        P += [(grp, f"M{k}", "PASS_LEG", {"d": "ret", "gbus": "gate_bus", "g": f"pg{k}", "s": f"ps{k}", "src": "0"},
               {"VT": f"vt_pass+({vth - VTH_MEAN:+.4f})", "CGD": "cgd_pass", "CGS": "23e-9-cgd_pass", "K": "k_pass"})]
    P += [(grp, "D_tvs", "TVS_5KP12CA", {"a": "ret", "b": "0"}, {})]

    # ---- gate stage -----------------------------------------------------
    grp = "5 GATE STAGE: OPA994, gain 1 + 6.98k/1k, 50 ohm terminator"
    P += [
        (grp, "R_term", "R", {"1": "out1", "2": "0"}, {"R": "R_term"}),
        (grp, "U_opa", "OPA994", {"inp": "out1", "inn": "fb", "out": "gate_bus", "vcc": "vop", "vee": "0"}, {}),
        (grp, "R_f", "R", {"1": "fb", "2": "gate_bus"}, {"R": "6.98kOhm"}),
        (grp, "R_g", "R", {"1": "fb", "2": "0"}, {"R": "1kOhm"}),
        (grp, "V_opamp", "VDC", {"positive": "vop", "negative": "0"}, {"DC": "V_opamp"}),
    ]

    # ---- sensing chain --------------------------------------------------
    grp = "6 SENSING: 10 ohm burden, AD8421 (G = 1), 49.9 ohm, cable to IN1"
    P += [
        (grp, "R_burden", "R", {"1": "lem_m", "2": "0"}, {"R": "10ohm"}),
        (grp, "U_ina", "AD8421", {"inp": "0", "inn": "lem_m", "ref": "0", "out": "ina_out"}, {}),
        (grp, "R_ser", "R", {"1": "ina_out", "2": "in1_src"}, {"R": "49.9ohm"}),
        (grp, "X_in1", "TEST_LINK", {"a": "in1_src", "b": "in1", "ctl": "in1_ok"}, {}),
        (grp, "C_cable", "C", {"1": "in1", "2": "0"}, {"C": "150pF"}),
    ]

    # ---- Red Pitaya -----------------------------------------------------
    grp = "7 RED PITAYA: firmware stand-in (PI, clamp, floor, snap, trip)"
    P += [
        (grp, "U_rp", "RP_SERVO", {"in1": "in1", "in2": "in2", "out1": "out1", "pol": "dio0", "run": "run", "ol": "ol", "u": "u_kcounts", "im": "i_meas"},
         {"KP": "kp", "KI": "ki", "CLAMP": "u_clamp", "FLOOR": "u_floor", "TRIP": "trip_amps", "SNAP": "snap_amps"}),
    ]

    # ---- logic board ----------------------------------------------------
    grp = "8 SAFETY LOGIC BOARD: 74HC04, CD74HC123, 74HC74, 74HC10"
    P += [
        (grp, "U_logic", "LOGIC_BOARD", {"dio0": "dio0", "dio1": "dio1", "dio6": "dio6", "rst": "rst", "pol": "pol", "npol": "npol", "dis": "dis", "ok": "hb_ok", "trip": "latch_trip"}, {}),
        (grp, "X_hb", "TEST_LINK", {"a": "dio6_rp", "b": "dio6", "ctl": "hb_wire"}, {}),
    ]

    # ---- test sources: firmware state machine, operator, faults -----------
    grp = "9 TEST SOURCES: what the firmware FSM, the operator and faults do (set by design variables)"
    pulse = lambda v1, v2, td, tr, tf, pw, per: {"V1": v1, "V2": v2, "TD": td, "TR": tr, "TF": tf, "PW": pw, "PER": per}
    P += [
        # the setpoint on IN2 is the sum of two sources in series: a first step, and an optional second one
        (grp, "V_setpoint", "VPULSE", {"positive": "sp_b", "negative": "0"}, pulse("0", "sp_amps/200", "t_sp_on", "sp_amps/slew_up*1s+10ns", "sp_fall", "t_sp_len", "10s")),
        (grp, "V_setpoint2", "VPULSE", {"positive": "in2", "negative": "sp_b"}, pulse("0", "sp2_amps/200", "t_sp2_on", "abs(sp2_amps)/slew2*1s+10ns", "1us", "10s", "20s")),
        (grp, "V_run", "VPULSE", {"positive": "run", "negative": "0"}, pulse("1", "0", "t_run_off", "10ns", "10ns", "t_run_len", "20s")),
        (grp, "V_openloop", "VPULSE", {"positive": "ol", "negative": "0"}, pulse("ol_kcounts", "ol_kcounts+ol_step", "t_ol_step", "t_ol_ramp", "10ns", "10s", "20s")),
        (grp, "V_dio0_pol", "VPULSE", {"positive": "dio0", "negative": "0"}, pulse("3.3", "0", "t_pol", "10ns", "10ns", "10s", "20s")),
        (grp, "V_dio1_en", "VPULSE", {"positive": "dio1", "negative": "0"}, pulse("0", "3.3", "10us", "10ns", "10ns", "t_en_off-10us", "t_en_off-10us+en_gap")),
        (grp, "V_dio6_hb", "VPULSE", {"positive": "dio6_rp", "negative": "0"}, pulse("0", "3.3", "1us", "10ns", "10ns", "524us", "1048us")),
        (grp, "V_reset", "VPULSE", {"positive": "rst", "negative": "0"}, pulse("0", "3.3", "3us", "10ns", "10ns", "4us", "t_rst_per")),
        (grp, "V_hb_wire", "VPULSE", {"positive": "hb_wire", "negative": "0"}, pulse("1", "0", "t_hb_off", "10ns", "10ns", "t_hb_len", "20s")),
        (grp, "V_in1_cable", "VPULSE", {"positive": "in1_ok", "negative": "0"}, pulse("1", "0", "t_in1_off", "10ns", "10ns", "10s", "20s")),
    ]
    return P


STD_KINDS = {"R", "L", "C", "VDC", "VPULSE"}
_SIDE_ANGLE = {"L": 0.0, "R": math.pi, "T": 1.5 * math.pi, "B": 0.5 * math.pi}   # direction from the pin's end to the body
_LABEL_ANGLE = {"L": 0, "R": 180, "B": 90, "T": 270}                             # which way a net label points


def make_symbols(sch):
    """Draw the block symbols (a box with named pins on four sides) and tie the models to them."""
    import re
    for name, (w, h, pins) in layout.SYMBOLS.items():
        arg = ["NAME:" + name, "ModTime:=", 1591858230, "Library:=", "", "ModSinceLib:=", False, "LibLocation:=", "Project",
               "HighestLevel:=", 1, "Normalize:=", False, "InitialLevels:=", [0, 1]]
        sch.osymbol_manager.Add(arg + [["NAME:Graphics"]])
        for pin, (x, y, side) in pins.items():
            # the pin, and its name written just beside its outer end
            tx, ty = (x * G + 0.0006, y * G) if side in "TB" else (x * G, y * G + 0.0006)
            arg.append(["NAME:PinDef", "Pin:=", [pin, x * G, y * G, _SIDE_ANGLE[side], "N", 0, 2 * G, False, 0, True, "", True, False, pin, True],
                        ["NAME:PropDisplayMap", "PinName:=", [2, 5, 1, "Text:=", [tx, ty, 0, 4, 5, False, "Arial", 0, pin, False, False,
                                                                              "ExtentRect:=", [0, 0, 0, 0, tx, ty, 0.002, 0.0018, 0, 0, 0]]]]])
        # the body: one rectangle (centre x, centre y, width, height). It must sit directly in the
        # one Graphics block -- nested or in a second block, Ansys stores the symbol with no body.
        arg.append(["NAME:Graphics", "Rect:=", [0, 0, 0, 0, w * G / 2, h * G / 2, w * G, h * G, 0, 0, 0]])
        sch.osymbol_manager.EditWithComps(name, arg, [])
    with open(LIB) as f:
        all_models = re.findall(r"^\.subckt\s+(\S+)", f.read(), re.M)
    models = ["NAME:Models"]
    for m in all_models:
        models += [m + ":=", [m in layout.SYMBOLS, m if m in layout.SYMBOLS else "", "", False]]
    sch.ocomponent_manager.ImportModelsFromFile(
        LIB.replace("\\", "/"),
        ["NAME:Options", "Mode:=", 2, "Overwrite:=", True, "SupportsSimModels:=", False, "LoadOnly:=", False, models])


def _pinmap(kind, comp):
    cpins = list(comp.pins)
    if kind in ("R", "L", "C"):      # "1" = left (or top, when drawn vertically), "2" = the other end
        order = sorted(cpins, key=lambda p: (round(p.location[0], 6), -round(p.location[1], 6)))
        return {"1": order[0], "2": order[1]}
    return {p.name: p for p in cpins}


def build_design(cir, config: str, first: bool = True) -> dict:
    """Draw one design as a wired circuit diagram. Returns {name: component}."""
    sch = cir.modeler.schematic
    P = parts(config)
    place, wires = layout.place(config), layout.wires(config)

    variables = dict(COMMON_VARS)
    variables.update(CONFIGS[config]["vars"])
    for name, (value, _doc) in variables.items():
        cir[name] = value
    if first:
        make_symbols(sch)

    # ---- place the parts -------------------------------------------------
    comps, pinloc, pinside, pinnet = {}, {}, {}, {}
    for _, name, kind, pins, props in P:
        x, y, angle = place[name]
        loc = [x * G, y * G]
        if kind == "R":
            c = sch.create_resistor(name, props["R"], location=loc, angle=angle)
        elif kind == "L":
            c = sch.create_inductor(name, props["L"], location=loc, angle=angle)
        elif kind == "C":
            c = sch.create_capacitor(name, props["C"], location=loc, angle=angle)
        elif kind == "VDC":
            c = sch.create_voltage_dc(name, props["DC"], location=loc)
        elif kind == "VPULSE":
            c = sch.create_voltage_pulse(name, [props[k] for k in ("V1", "V2", "TD", "TR", "TF", "PW", "PER")], location=loc)
        else:
            c = sch.create_component(None, component_library=None, component_name=kind, location=loc)
            c.set_property("InstanceName", name)
            for k, v in props.items():
                c.set_property(k, v)
            for where in ("Center", "Top"):      # show the part's name on the block
                try:
                    sch.oeditor.ChangeProperty(["NAME:AllTabs", ["NAME:PropDisplayPropTab", ["NAME:PropServers", c.composed_name],
                                                ["NAME:NewProps", ["NAME:InstanceName", "Format:=", "Value", "Location:=", where]]]])
                    break
                except Exception:
                    continue
        comps[name] = c
        pm = _pinmap(kind, c)
        if set(pm) != set(pins):
            raise RuntimeError(f"{name} ({kind}): model pins {sorted(pm)} != table pins {sorted(pins)}")
        cx = sum(p.location[0] for p in pm.values()) / len(pm)
        cy = sum(p.location[1] for p in pm.values()) / len(pm)
        for pname, net in pins.items():
            px, py = pm[pname].location
            key = f"{name}.{pname}"
            pinloc[key] = (round(px / G), round(py / G))
            pinnet[key] = net
            if kind in layout.SYMBOLS:
                pinside[key] = layout.SYMBOLS[kind][2][pname][2]
            elif abs(px - cx) >= abs(py - cy):
                pinside[key] = "L" if px < cx else "R"
            else:
                pinside[key] = "B" if py < cy else "T"

    # ---- draw the wires --------------------------------------------------
    wired = set()
    for net, paths in wires.items():
        for path in paths:
            pts = []
            for tok in path:
                if isinstance(tok, str):
                    if pinnet[tok] != net:
                        raise RuntimeError(f"layout: wire of net {net} goes to {tok}, which the parts table puts on {pinnet[tok]}")
                    wired.add(tok)
                    pt = pinloc[tok]
                elif tok[0] == "x":
                    pt = (tok[1], pts[-1][1])
                elif tok[0] == "y":
                    pt = (pts[-1][0], tok[1])
                else:
                    pt = tuple(tok)
                if pts and pt[0] != pts[-1][0] and pt[1] != pts[-1][1]:
                    raise RuntimeError(f"layout: net {net}: wire from {pts[-1]} to {pt} ({tok}) is not horizontal or vertical")
                if not pts or pt != pts[-1]:
                    pts.append(pt)
            if not sch.create_wire([[x * G, y * G] for x, y in pts], name=net):
                raise RuntimeError(f"could not draw wire of net {net}: {pts}")

    # ---- every pin no wire reaches: ground symbol or net label ------------
    for key, net in pinnet.items():
        if key in wired:
            continue
        x, y = pinloc[key]
        if net == "0":
            sch.create_gnd(location=[x * G, (y - 1) * G])
        else:
            sch.create_page_port(net, location=[x * G, y * G], angle=_LABEL_ANGLE[pinside[key]])

    # ---- titles ----------------------------------------------------------
    for x, y, text in layout.texts(config):
        try:
            sch.oeditor.CreateText(
                ["NAME:TextData", "X:=", x * G, "Y:=", y * G, "Size:=", 12, "Angle:=", 0, "Text:=", text,
                 "Color:=", 0, "Id:=", 0, "ShowRect:=", False, "X1:=", 0, "Y1:=", 0, "X2:=", 0, "Y2:=", 0,
                 "RectLineWidth:=", 0, "RectBorderColor:=", 0, "RectFill:=", 0, "RectColor:=", 0],
                ["NAME:Attributes", "Page:=", 1])
        except Exception:
            pass

    # ---- check: every pin is on the net the parts table asks for ----------
    wrong = []
    for _, name, kind, pins, _ in P:
        pm = _pinmap(kind, comps[name])
        for pname, net in pins.items():
            got = pm[pname].net
            if got != net:
                wrong.append((name, pname, "wanted " + net, "got " + str(got)))
    if wrong:
        raise RuntimeError("schematic nets differ from the parts table:\n" + "\n".join(map(str, wrong)))

    from ansys.aedt.core.generic.constants import Setups
    for sname, step, stop in (("Tran_600us", "50ns", "600us"), ("Tran_3ms", "200ns", "3ms"), ("Tran_12ms", "500ns", "12ms")):
        s = cir.create_setup(sname, Setups.NexximTransient)
        s.props["TransientData"] = [step, stop]
        s.update()
    n_lab = len(pinnet) - len(wired)
    print(f"[{config}] {len(P)} parts, {sum(len(v) for v in wires.values())} wires, {n_lab} labelled/grounded pins, every pin on its net")
    return comps


def open_project(new: bool, design: str, non_graphical: bool = True):
    from ansys.aedt.core import Circuit
    project = project_of(design)
    if new:
        for ext in ("", ".lock"):
            if os.path.exists(project + ext):
                os.remove(project + ext)
    return Circuit(project=project, design=design, version=AEDT_VERSION, non_graphical=non_graphical,
                   new_desktop=True, student_version=True)


def main(argv):
    configs = argv or [d for designs in PROJECTS.values() for d in designs]
    # one Ansys session per project file; a project is rebuilt from scratch with the designs named
    for fname in PROJECTS:
        mine = [c for c in configs if project_of(c) == os.path.join(HERE, fname)]
        cir = None
        try:
            for i, config in enumerate(mine):
                if cir is None:
                    cir = open_project(new=True, design=config)
                else:
                    from ansys.aedt.core import Circuit
                    cir = Circuit(project=project_of(config), design=config, version=AEDT_VERSION, non_graphical=True,
                                  new_desktop=False, student_version=True)
                build_design(cir, config, first=(i == 0))
                cir.save_project()
        finally:
            if cir is not None:
                cir.release_desktop(close_projects=True, close_desktop=True)
        if mine:
            print("wrote", os.path.join(HERE, fname), mine)


if __name__ == "__main__":
    main(sys.argv[1:])
