"""Where every part sits on the schematic and how the wires run.

Coordinates are in schematic grid units (1 unit = 2.54 mm), x to the right,
y up. build_project.py reads these from here:

  SYMBOLS        the drawing of each block: a box with named pins on its
                 four sides (so current paths can be drawn top to bottom)
  place(config)  {part name: (x, y, angle)}
  wires(config)  {net name: [path, path, ...]}  -- each path is a list of
                 points the wire runs through:
                    "Q1.d"        a pin of a part
                    (x, y)        a point on the sheet
                    ("x", 84)     go horizontally to x = 84
                    ("y", 30)     go vertically to y = 30
  texts(config)  titles written on the sheet

A pin that no wire reaches gets a small NET LABEL instead (pins with the
same label are connected), or a ground symbol if it is on net "0". That is
used on purpose for the logic lines to the four bridge drivers, the 5 V
logic supply, the LEM output and the test sources, where wires would cross
the power path and make the drawing harder to read.
"""

# name: (body width, body height, {pin: (x, y, side)})   side: L, R, T, B
SYMBOLS = {
    "BRIDGE_SWITCH": (6, 6, {"d": (3, 8, "T"), "s": (3, -2, "B"), "inp": (-2, 4, "L"), "inn": (-2, 2, "L"), "v5": (8, 4, "R"), "g": (8, 2, "R")}),
    "PASS_LEG": (4, 6, {"d": (2, 8, "T"), "src": (2, -2, "B"), "gbus": (-2, 3, "L"), "g": (6, 4, "R"), "s": (6, 2, "R")}),
    "DC_SUPPLY": (6, 8, {"p": (3, 10, "T"), "n": (3, -2, "B")}),
    "VARISTOR_S20K20": (4, 2, {"a": (-2, 1, "L"), "b": (6, 1, "R")}),
    "TVS_5KP12CA": (2, 4, {"a": (1, 6, "T"), "b": (1, -2, "B")}),
    "TVS_5KP30A": (2, 4, {"k": (1, 6, "T"), "a": (1, -2, "B")}),
    "SCHOTTKY_DSS2X101": (4, 2, {"a": (-2, 1, "L"), "k": (6, 1, "R")}),
    "LEM_LF310S": (6, 4, {"in": (-2, 2, "L"), "ip": (8, 2, "R"), "m": (3, -2, "B")}),
    "AD8421": (6, 6, {"inn": (-2, 4, "L"), "inp": (-2, 2, "L"), "out": (8, 3, "R"), "ref": (3, -2, "B")}),
    "OPA994": (6, 6, {"inp": (-2, 4, "L"), "inn": (-2, 2, "L"), "out": (8, 3, "R"), "vcc": (3, 8, "T"), "vee": (3, -2, "B")}),
    "LOGIC_BOARD": (10, 10, {"dio0": (-2, 8, "L"), "dio1": (-2, 6, "L"), "dio6": (-2, 4, "L"), "rst": (-2, 2, "L"),
                             "pol": (12, 8, "R"), "npol": (12, 6, "R"), "dis": (12, 4, "R"), "ok": (3, -2, "B"), "trip": (7, -2, "B")}),
    "RP_SERVO": (12, 12, {"in1": (-2, 10, "L"), "in2": (-2, 8, "L"), "run": (-2, 4, "L"), "ol": (-2, 2, "L"),
                          "out1": (14, 10, "R"), "pol": (14, 6, "R"), "u": (4, -2, "B"), "im": (8, -2, "B")}),
    "TEST_LINK": (4, 2, {"a": (-2, 1, "L"), "b": (6, 1, "R"), "ctl": (2, -2, "B")}),
}

SOURCES = ["V_setpoint", "V_run", "V_openloop", "V_dio0_pol", "V_dio1_en", "V_dio6_hb", "V_reset", "V_hb_wire", "V_in1_cable", "V_logic5"]
LEG_X = [46, 60, 74, 88, 102, 116]   # left edge of each pass-bank leg block (room for the probe labels between legs)


def base(config):
    """'bench_ft' -> 'bench': the design this one is a variant of."""
    return config.split("_")[0]


def fast(config):
    """True for the designs with the fast turn-off parts (5KP30A + DSS2X101-015A)."""
    return config.endswith("_ft")


def x_b(config):
    """x of the bridge's right leg (node B); the MOT pair needs a wider coil row."""
    return 77 if base(config) == "bench" else 99


def place(config):
    xb = x_b(config)
    ft, config = fast(config), base(config)
    P = {
        # supply and leads (top left)
        "PSU": (0, 62, 0), "R_lead": (12, 84, 0), "L_lead": (22, 84, 0), "R_lead_ac": (22, 90, 0),
        # H-bridge: Q1/Q2 high side, Q3/Q4 low side
        "Q1": (40, 70, 0), "Q2": (xb - 3, 70, 0), "Q3": (40, 36, 0), "Q4": (xb - 3, 36, 0),
        # damper across the coil (row y = 58)
        "R_damp1": (50, 58, 0), "R_damp2": (58, 58, 0), "C_damp": (66, 58, 0),
        # pass bank under the bridge
        **{f"M{k + 1}": (x, 10, 0) for k, x in enumerate(LEG_X)},
        "D_tvs": (128, 14, 0),
        # gate stage (left of the bank)
        "U_opa": (22, 10, 0), "R_f": (26, -2, 0), "R_g": (16, -6, 90), "V_opamp": (12, 24, 0), "R_term": (8, 10, 90),
        # Red Pitaya and the sensing chain feeding IN1 (far left)
        "U_rp": (-30, 4, 0),
        "R_burden": (-86, 11, 90), "U_ina": (-76, 11, 0), "R_ser": (-62, 14, 0), "X_in1": (-56, 13, 0), "C_cable": (-42, 10, 90),
        # safety logic board (left of the bridge)
        "U_logic": (-8, 44, 0), "X_hb": (-20, 47, 0),
    }
    if config == "bench":
        P.update({"RV1": (58, 63, 0), "LEM": (47, 48, 0), "L_coil": (61, 50, 0), "R_coil": (69, 50, 0)})
    else:
        P.update({"L_coil1": (49, 50, 0), "R_coil1": (57, 50, 0), "LEM": (63, 48, 0), "L_coil": (77, 50, 0), "R_coil": (85, 50, 0),
                  "RV1": (51, 53, 0), "RV2": (79, 53, 0)})
    for k, name in enumerate(SOURCES):
        P[name] = (-86 + 12 * k, -24, 0)
    P["V_setpoint2"] = (-86, -18, 0)      # in series on top of V_setpoint
    if ft:
        P["D_block"] = (32, 83, 0)            # in the supply + lead, just before the rail bar
        P["D_clamp"] = (xb + 18, 56, 0)       # rail bar (top) to return bar (bottom), right of the bridge
    return P


def wires(config):
    xb = x_b(config)
    ft, config = fast(config), base(config)
    W = {
        # ---- supply -> rail bar -------------------------------------------
        "sup_p": [["PSU.p", ("y", 84), "R_lead.1"]],
        "lead_m": [["R_lead.2", "L_lead.1"], [(17, 84), ("y", 90), "R_lead_ac.1"]],
        "rail": [["L_lead.2", ("x", xb), "Q2.d"], ["Q1.d", ("y", 84)], ["R_lead_ac.2", ("x", 27), ("y", 84)]],
        # ---- bridge legs ---------------------------------------------------
        "A": [["Q1.s", "Q3.d"], [(43, 58), "R_damp1.1"]],
        "B": [["Q2.s", "Q4.d"], ["C_damp.2", ("x", xb)], ["R_coil.2", ("x", xb)]],
        "dmp1": [["R_damp1.2", "R_damp2.1"]],
        "dmp2": [["R_damp2.2", "C_damp.1"]],
        "coil_a": [["LEM.ip", "L_coil.1"]],
        "coil_m": [["L_coil.2", "R_coil.1"]],
        # ---- return bar -> pass-bank drain bar ------------------------------
        "ret": [["Q3.s", ("y", 30), ("x", xb), "Q4.s"], [(60, 30), ("y", 24)], [(48, 24), ("x", 129)], ["D_tvs.a", ("y", 24)]]
               + [[f"M{k}.d", ("y", 24)] for k in range(1, 7)],
        # ---- gate stage -> gate bus ----------------------------------------
        "gate_bus": [["U_opa.out", ("x", 34), ("y", 3), ("x", 114)], ["R_f.2", ("x", 34), ("y", 3)]]
                    + [[f"M{k}.gbus", ("y", 3)] for k in range(1, 7)],
        "fb": [["U_opa.inn", ("x", 16), ("y", -2), "R_f.1"], [(16, -2), "R_g.1"]],
        "vop": [["U_opa.vcc", ("y", 30), ("x", 12), "V_opamp.positive"]],
        "out1": [["U_rp.out1", "U_opa.inp"], [(8, 14), "R_term.1"]],
        # ---- sensing chain -> IN1 ------------------------------------------
        "lem_m": [["U_ina.inn", ("x", -86), "R_burden.1"]],
        "ina_out": [["U_ina.out", "R_ser.1"]],
        "in1_src": [["R_ser.2", "X_in1.a"]],
        "in1": [["X_in1.b", "U_rp.in1"], [(-42, 14), "C_cable.1"]],
        # ---- heartbeat through the "pullable wire" -------------------------
        "dio6": [["X_hb.b", "U_logic.dio6"]],
        # ---- the two setpoint sources in series ----------------------------
        "sp_b": [["V_setpoint.positive", "V_setpoint2.negative"]],
    }
    if config == "bench":
        W["A"] += [[(43, 64), "RV1.a"], [(43, 50), "LEM.in"]]
        W["B"] += [["RV1.b", ("x", xb)]]
    else:
        W["A"] += [[(43, 54), "RV1.a"], [(43, 50), "L_coil1.1"]]
        W["B"] += [["RV2.b", ("x", xb)]]
        W["c1"] = [["L_coil1.2", "R_coil1.1"]]
        W["mid_a"] = [["R_coil1.2", "LEM.in"], ["RV1.b", ("x", 60), ("y", 50)]]
        W["coil_a"] += [["RV2.a", ("x", 73), ("y", 50)]]
    if ft:
        # the lead now ends at the blocking diode; the rail bar starts behind it
        W["rail_in"] = [["L_lead.2", "D_block.a"], ["R_lead_ac.2", ("x", 27), ("y", 84)]]
        W["rail"] = [["D_block.k", ("x", xb), "Q2.d"], ["Q1.d", ("y", 84)], ["D_clamp.k", ("y", 84), ("x", xb)]]
        W["ret"] += [["D_clamp.a", ("y", 30), ("x", xb)]]
        if config == "mot":       # one damper resistor (frees a part for the 50-component limit)
            del W["dmp1"]
            W["dmp2"] = [["R_damp1.2", "C_damp.1"]]
    return W


def texts(config):
    xb = x_b(config)
    ft, config = fast(config), base(config)
    extra = [(27, 80, "DSS2X101-015A"), (xb + 22, 58, "5KP30A")] if ft else []
    supply = "SUPPLY: bench supply 5 V / 3 A and its leads" if config == "bench" else "SUPPLY: Mean Well SE-600-5 and 4/0 cable"
    coil = "COIL: test coil, RC damper, varistor, LEM" if config == "bench" else "MOT COILS: varistor per coil, LEM between, PROPOSED damper"
    return extra + [
        (0, 96, supply),
        (40, 96, "RAIL BAR"),
        (40, 88, "H-BRIDGE: Q1-Q4 = IXFN420N10T + UCC5350SB driver + RO-0515S supply"),
        (46, 68, coil),
        (xb + 6, 50, "node B"), (34, 56, "node A"),
        (50, 32, "RETURN BAR = pass-bank DRAIN BAR"),
        (131, 16, "5KP12CA"),
        (46, 0, "PASS BANK: M1-M6 = IXTN200N10L2 + 24.9 ohm gate + 47 k pull-down + 0.03 ohm ballast.  Ground symbols = SOURCE BAR = supply minus"),
        (6, 34, "GATE STAGE: OPA994, gain 1 + 6.98k/1k"),
        (-32, 20, "RED PITAYA (firmware stand-in)"),
        (-88, 20, "SENSING: burden, AD8421 (G = 1), 49.9 ohm, cable to IN1"),
        (-22, 58, "SAFETY LOGIC BOARD"),
        (-80, -14, "TEST SOURCES: what the firmware state machine, the operator and faults do (set by the design variables)"),
    ]
