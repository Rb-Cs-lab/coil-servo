"""Build the Ansys Electronics Desktop project of the coil-servo power stage.

    ansys\\.venv\\Scripts\\python.exe ansys\\build_project.py            # both designs
    ansys\\.venv\\Scripts\\python.exe ansys\\build_project.py bench      # one design

Writes ansys/coil_servo.aedt (open it with Ansys Electronics Desktop
Student 2025 R2) with one "Circuit" design per configuration:

    bench  the power stage exactly as it is wired on the bench now
           (3 A bench supply, 45 uH test coil, 3.2 ohm + 10 uF damper)
    mot    the same electronics on the SE-600-5 with the two MOT coils
           (planned Test 4 wiring -- NOT built yet, values provisional)

The circuit is described once, in PARTS below, as a table of
(group, name, kind, pins -> net names, values). The schematic is drawn from
that table: every pin carries a net label, and pins with the same label are
connected. The part models are in models/coil_servo_parts.lib.
See README.md in this folder.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "models", "coil_servo_parts.lib")
PROJECT = os.path.join(HERE, "coil_servo.aedt")
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
}

CONFIGS = {
    "bench": {
        "vars": {
            "V_sup": ("5.06", "bench supply open-circuit voltage (measured)"),
            "I_lim": ("3", "bench supply current limit, amps"),
            "C_sup": ("100e-6", "supply output capacitance, farads (INFERRED from the surge trace)"),
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


def parts(config: str) -> list[tuple]:
    """The circuit as a table: (group, name, kind, {pin: net}, {property: value})."""
    P: list[tuple] = []

    # ---- supply and leads ------------------------------------------------
    grp = "1 SUPPLY: bench supply 5 V / 3 A + leads" if config == "bench" else "1 SUPPLY: Mean Well SE-600-5 + 4/0 cable"
    P += [
        (grp, "PSU", "DC_SUPPLY", {"p": "sup_p", "n": "0"}, {"VSET": "V_sup", "ILIM": "I_lim", "COUT": "C_sup"}),
        (grp, "R_lead", "R", {"1": "sup_p", "2": "lead_m"}, {"R": "R_lead"}),
        (grp, "L_lead", "L", {"1": "lead_m", "2": "rail"}, {"L": "L_lead"}),
        # not a part: high-frequency loss of the leads (skin effect), so lead ringing dies in ~10 us as on the bench
        (grp, "R_lead_ac", "R", {"1": "lead_m", "2": "rail"}, {"R": "R_lead_ac"}),
        (grp, "V_logic5", "VDC", {"positive": "v5", "negative": "0"}, {"DC": "5V"}),
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

    # ---- pass bank ------------------------------------------------------
    grp = "4 PASS BANK: 6 x IXTN200N10L2, 24.9 ohm gate, 47 k pull-down, MP930 0.03 ohm ballast, 5KP12CA"
    # One block per leg (MOSFET + gate resistor + pull-down + ballast, PASS_LEG).
    for k, vth in enumerate(VTH_3MA, 1):
        P += [(grp, f"M{k}", "PASS_LEG", {"d": "ret", "gbus": "gate_bus", "g": f"pg{k}", "s": f"ps{k}", "src": "0"},
               {"VT": f"vt_pass+({vth - VTH_MEAN:+.4f})"})]
    P += [(grp, "D_tvs", "TVS_5KP12CA", {"a": "ret", "b": "0"}, {})]

    # ---- gate stage -----------------------------------------------------
    grp = "5 GATE STAGE: OPA994, gain 1 + 6.98k/1k, 50 ohm terminator"
    P += [
        (grp, "R_term", "R", {"1": "out1", "2": "0"}, {"R": "50ohm"}),
        (grp, "U_opa", "OPA994", {"inp": "out1", "inn": "fb", "out": "gate_bus", "vcc": "vop", "vee": "0"}, {}),
        (grp, "R_f", "R", {"1": "gate_bus", "2": "fb"}, {"R": "6.98kOhm"}),
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
         {"KP": "kp", "KI": "ki", "CLAMP": "u_clamp", "FLOOR": "u_floor", "TRIP": "trip_amps"}),
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
        (grp, "V_setpoint", "VPULSE", {"positive": "in2", "negative": "0"}, pulse("0", "sp_amps/200", "t_sp_on", "sp_amps/slew_up*1s+10ns", "1us", "t_sp_len", "10s")),
        (grp, "V_run", "VPULSE", {"positive": "run", "negative": "0"}, pulse("1", "0", "t_run_off", "10ns", "10ns", "10s", "20s")),
        (grp, "V_openloop", "VDC", {"positive": "ol", "negative": "0"}, {"DC": "ol_kcounts"}),
        (grp, "V_dio0_pol", "VDC", {"positive": "dio0", "negative": "0"}, {"DC": "3.3V"}),
        (grp, "V_dio1_en", "VPULSE", {"positive": "dio1", "negative": "0"}, pulse("0", "3.3", "10us", "10ns", "10ns", "t_en_off-10us", "20s")),
        (grp, "V_dio6_hb", "VPULSE", {"positive": "dio6_rp", "negative": "0"}, pulse("0", "3.3", "1us", "10ns", "10ns", "524us", "1048us")),
        (grp, "V_reset", "VPULSE", {"positive": "rst", "negative": "0"}, pulse("0", "3.3", "3us", "10ns", "10ns", "4us", "20s")),
        (grp, "V_hb_wire", "VPULSE", {"positive": "hb_wire", "negative": "0"}, pulse("1", "0", "t_hb_off", "10ns", "10ns", "10s", "20s")),
        (grp, "V_in1_cable", "VPULSE", {"positive": "in1_ok", "negative": "0"}, pulse("1", "0", "t_in1_off", "10ns", "10ns", "10s", "20s")),
    ]
    return P


def models_used(P) -> list[str]:
    std = {"R", "L", "C", "VDC", "VPULSE"}
    out = []
    for _, _, kind, _, _ in P:
        if kind not in std and kind not in out:
            out.append(kind)
    return out


def build_design(cir, config: str) -> dict:
    """Draw one design. Returns {name: component}."""
    sch = cir.modeler.schematic
    P = parts(config)

    variables = dict(COMMON_VARS)
    variables.update(CONFIGS[config]["vars"])
    for name, (value, _doc) in variables.items():
        cir[name] = value

    for model in models_used(P):
        ok = sch.create_component_from_spicemodel(LIB, model=model, create_component=False)
        if not ok:
            raise RuntimeError(f"could not import model {model} from {LIB}")

    comps = {}
    wrong = []
    groups: dict[str, list] = {}
    for row in P:
        groups.setdefault(row[0], []).append(row)

    DX, DY, NCOL = 16 * G, 14 * G, 6
    y_top = 0.0
    for gname, rows in groups.items():
        try:
            cir.modeler.schematic.oeditor.CreateText(
                ["NAME:TextData", "X:=", 0.0, "Y:=", y_top + 6 * G, "Size:=", 14, "Angle:=", 0, "Text:=", gname,
                 "Color:=", 0, "Id:=", 0, "ShowRect:=", False, "X1:=", 0, "Y1:=", 0, "X2:=", 0, "Y2:=", 0,
                 "RectLineWidth:=", 0, "RectBorderColor:=", 0, "RectFill:=", 0, "RectColor:=", 0],
                ["NAME:Attributes", "Page:=", 1])
        except Exception:
            pass
        for i, (_, name, kind, pins, props) in enumerate(rows):
            loc = [(i % NCOL) * DX, y_top - (i // NCOL) * DY]
            if kind == "R":
                c = sch.create_resistor(name, props["R"], location=loc)
            elif kind == "L":
                c = sch.create_inductor(name, props["L"], location=loc)
            elif kind == "C":
                c = sch.create_capacitor(name, props["C"], location=loc)
            elif kind == "VDC":
                c = sch.create_voltage_dc(name, props["DC"], location=loc)
            elif kind == "VPULSE":
                c = sch.create_voltage_pulse(name, [props[k] for k in ("V1", "V2", "TD", "TR", "TF", "PW", "PER")], location=loc)
            else:
                c = sch.create_component(None, component_library=None, component_name=kind, location=loc)
                c.set_property("InstanceName", name)
                for k, v in props.items():
                    c.set_property(k, v)
            comps[name] = c
            cpins = list(c.pins)
            if kind in ("R", "L", "C"):
                order = sorted(cpins, key=lambda p: (round(p.location[0], 6), -round(p.location[1], 6)))
                pinmap = {"1": order[0], "2": order[1]}
            else:
                pinmap = {p.name: p for p in cpins}
            if set(pinmap) != set(pins):
                raise RuntimeError(f"{name} ({kind}): model pins {sorted(pinmap)} != table pins {sorted(pins)}")
            cx = sum(p.location[0] for p in cpins) / len(cpins)
            for pname, net in pins.items():
                x, y = pinmap[pname].location
                if net == "0":
                    sch.create_gnd(location=[x, y - G])
                else:
                    sch.create_page_port(net, location=[x, y], angle=0 if x <= cx else 180)
        y_top -= ((len(rows) - 1) // NCOL + 1) * DY + 6 * G

    # check that every pin landed on the net the table asks for
    for _, name, kind, pins, _ in P:
        c = comps[name]
        cpins = list(c.pins)
        if kind in ("R", "L", "C"):
            order = sorted(cpins, key=lambda p: (round(p.location[0], 6), -round(p.location[1], 6)))
            pinmap = {"1": order[0], "2": order[1]}
        else:
            pinmap = {p.name: p for p in cpins}
        for pname, net in pins.items():
            got = pinmap[pname].net
            if got != net:
                wrong.append((name, pname, net, got))
    if wrong:
        raise RuntimeError("schematic nets differ from the table:\n" + "\n".join(map(str, wrong)))

    from ansys.aedt.core.generic.constants import Setups
    for sname, step, stop in (("Tran_600us", "50ns", "600us"), ("Tran_3ms", "200ns", "3ms"), ("Tran_12ms", "500ns", "12ms")):
        s = cir.create_setup(sname, Setups.NexximTransient)
        s.props["TransientData"] = [step, stop]
        s.update()
    print(f"[{config}] {len(P)} parts placed, {len(variables)} design variables, all pins on their nets")
    return comps


def open_project(new: bool, design: str, non_graphical: bool = True):
    from ansys.aedt.core import Circuit
    if new:
        for ext in ("", ".lock"):
            if os.path.exists(PROJECT + ext):
                os.remove(PROJECT + ext)
    return Circuit(project=PROJECT, design=design, version=AEDT_VERSION, non_graphical=non_graphical,
                   new_desktop=True, student_version=True)


def main(argv):
    configs = argv or ["bench", "mot"]
    cir = None
    try:
        for i, config in enumerate(configs):
            if cir is None:
                cir = open_project(new=True, design=config)
            else:
                from ansys.aedt.core import Circuit
                cir = Circuit(project=PROJECT, design=config, version=AEDT_VERSION, non_graphical=True,
                              new_desktop=False, student_version=True)
            build_design(cir, config)
            cir.save_project()
    finally:
        if cir is not None:
            cir.release_desktop(close_projects=True, close_desktop=True)
    print("wrote", PROJECT)


if __name__ == "__main__":
    main(sys.argv[1:])
