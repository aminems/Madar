"""MADAR energy model. Analytical activity over a compiled single-ring program,
scaled by the per-event energies in energy_costs.py; a baseline of
instruction_count x pJ/instruction; and the crossover where they meet. Pure
accounting over the compiler's output -- model.py is not touched."""
from sim import energy_costs as ec

def _adv_cycles(prog, rid):
    return sum(ph["cycles"] for ph in prog["run"] if rid in ph["advance"])

def _toggling(P, occupied):
    """Slots whose content changes per cycle in a circulating ring, under
    clock-gating: a slot toggles unless it and its predecessor are both empty, so
    the count is (occupied slots + number of empty runs) -- a run of empty slots
    shifts bubble-to-bubble and is gated off. This is the exact first-order toggle
    count for a given seating, not an approximation."""
    S = sorted(occupied)
    o = len(S)
    if o == 0:
        return 0
    if o >= P:
        return P
    if o == 1:
        return min(2, P)          # the lone packet toggles its slot and the one it vacates
    runs = sum(1 for i in range(o) if (S[(i + 1) % o] - S[i]) % P > 1)
    return o + runs


def activity(prog, tier):
    """Activity of a compiled program (single- or multi-ring) under ring `tier`
    ('shift', 'shift_opt', or 'sram'). Returns shift_events, alu_ops, mul_ops,
    xfer_ops (COPY-relay + inter-ring transfer firings), cycles, P, R. Rotation is
    CLOCK-GATED: on a shift-register ring only slots whose content changes
    dissipate a word-shift each cycle (_toggling); the SRAM rotating-pointer tier
    reads and writes each occupied slot once per revolution. Each instruction on
    ring r fires once per revolution of r."""
    rings = prog["config"]["rings"]
    Pmap = {r["id"]: r["period"] for r in rings}
    occ = {}
    for e in prog["seed"]:
        occ.setdefault(e["ring"], set()).add(e["slot"])
    shift_events = 0
    for r in rings:
        rid = r["id"]; P = r["period"]; adv = _adv_cycles(prog, rid)
        if tier == "sram":
            shift_events += 2 * adv     # rotating pointer: one read + one write per cycle
        else:
            shift_events += _toggling(P, occ.get(rid, ())) * adv
    alu = mul = xfer = 0
    for e in prog["seed"]:
        pk = e["packet"]
        if pk.get("kind") != "instr":
            continue
        P = Pmap[e["ring"]]
        R = _adv_cycles(prog, e["ring"]) // P if P else 0
        op = pk["op"]
        if op == "MUL":
            mul += R
        elif op in ("ADD", "SUB", "CMPLT"):
            alu += R
        elif op == "XFER":
            xfer += R
    P0 = rings[0]["period"]
    cycles = _adv_cycles(prog, rings[0]["id"])
    return {"shift_events": shift_events, "alu_ops": alu, "mul_ops": mul,
            "xfer_ops": xfer, "cycles": cycles, "P": P0,
            "R": cycles // P0 if P0 else 0}

def madar_energy(act, tier):
    """pJ for a MADAR run: rotation (ring shifts) + compute (ALU/MUL station ops)
    + relay (each COPY-relay / inter-ring transfer is one 64-bit word move priced
    at the tier's per-word cost)."""
    e_shift = (ec.E_SRAM_ACCESS if tier == "sram"
               else ec.E_WORD_SHIFT_OPT if tier == "shift_opt"
               else ec.E_WORD_SHIFT)
    rotation = act["shift_events"] * e_shift
    compute = act["alu_ops"] * ec.E_ALU + act["mul_ops"] * ec.E_MUL
    relay = act.get("xfer_ops", 0) * e_shift
    return {"rotation": rotation, "compute": compute, "relay": relay,
            "total": rotation + compute + relay}

def instr_count(kernel):
    """Dynamic instruction count for the in-order baseline computing the same
    kernel. Loop: setup loads + trip x (body + updates + LOOP_OVERHEAD).
    Straight-line: one arithmetic instr per op + one load per input."""
    if "loop" in kernel:
        lp = kernel["loop"]
        setup = len(lp["state"]) + len(lp.get("consts", []))
        per_iter = len(lp.get("body", [])) + len(lp["updates"]) + ec.LOOP_OVERHEAD
        return setup + lp["trip"] * per_iter
    return len(kernel["ops"]) + len(kernel.get("inputs", []))

def baseline_energy(kernel, e_per_instr):
    return instr_count(kernel) * e_per_instr

def crossover(act, tier, kernel):
    """Baseline pJ/instruction at which baseline_energy == MADAR total."""
    n = instr_count(kernel)
    return madar_energy(act, tier)["total"] / n if n else float("inf")
