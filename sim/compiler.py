"""MADAR single-ring scheduling compiler. Places a kernel's packets on one ring
by backtracking (instructions first, then each value into the intersection of
its consumers' 1..W windows) and accepts a candidate by running its emitted
program.json on the madar/sim model and checking the output == the reference."""
from sim.model import W_WIN
from sim import program as progmod
from sim.kernel import reference as kernel_reference

class CompileError(Exception):
    pass

def _items(kernel):
    """Normalize a kernel. Returns (P, seeded, instrs, outputs, run_kind).
    seeded: {value_name: payload} for inputs/consts/state inits.
    instrs: list of {result, op, a, b}. (result is an op-id/temp for ops, or a
            state name for loop updates.)  outputs: list of value names.
    run_kind: ('settle',) or ('loop', trip)."""
    P = kernel["ring"]["period"]
    seeded = {}; instrs = []
    if "loop" in kernel:
        lp = kernel["loop"]
        for s in lp["state"]:
            seeded[s["name"]] = s["init"]
        for c in lp.get("consts", []):
            seeded[c["name"]] = c["value"]
        for op in lp.get("body", []):
            instrs.append({"result": op["id"], "op": op["op"], "a": op["a"], "b": op["b"]})
        for u in lp["updates"]:
            instrs.append({"result": u["state"], "op": u["op"], "a": u["a"], "b": u["b"]})
        return P, seeded, instrs, list(lp["outputs"]), ("loop", lp["trip"])
    for c in kernel.get("inputs", []):
        seeded[c["name"]] = c["value"]
    for c in kernel.get("consts", []):
        seeded[c["name"]] = c["value"]
    for op in kernel["ops"]:
        instrs.append({"result": op["id"], "op": op["op"], "a": op["a"], "b": op["b"]})
    return P, seeded, instrs, list(kernel["outputs"]), ("settle",)

def _values(seeded, instrs):
    """All value-packet names: seeded values plus instruction results."""
    vals = set(seeded.keys())
    for ins in instrs:
        vals.add(ins["result"])
    return sorted(vals)

def _place(P, values, instrs):
    """Yield (islot, vslot) dicts. Instructions placed first (distinct slots),
    then each value into the intersection of its referencing instrs' windows."""
    refs = {nm: [] for nm in values}
    for k, ins in enumerate(instrs):
        for nm in (ins["a"], ins["b"], ins["result"]):
            if nm in refs and k not in refs[nm]:
                refs[nm].append(k)
    used = [False] * P; islot = {}; vslot = {}; vlist = list(values)

    def place_values(j):
        if j == len(vlist):
            yield (dict(islot), dict(vslot)); return
        nm = vlist[j]
        for s in range(P):
            if used[s]:
                continue
            if all(1 <= (s - islot[k]) % P <= W_WIN for k in refs[nm]):
                used[s] = True; vslot[nm] = s
                for sol in place_values(j + 1):
                    yield sol
                del vslot[nm]; used[s] = False

    def place_instrs(k):
        if k == len(instrs):
            for sol in place_values(0):
                yield sol
            return
        for s in range(P):
            if used[s]:
                continue
            used[s] = True; islot[k] = s
            for sol in place_instrs(k + 1):
                yield sol
            del islot[k]; used[s] = False

    for sol in place_instrs(0):
        yield sol

def _program(P, seeded, instrs, islot, vslot):
    stations = [{"type": "alu", "ring": 0, "pos": 0}]
    if any(ins["op"] == "MUL" for ins in instrs):
        mul_pos = 8 if P > 8 else max(1, P // 2)
        stations.append({"type": "mul", "ring": 0, "pos": mul_pos})
    seed = []
    for nm, val in seeded.items():
        seed.append({"ring": 0, "slot": vslot[nm],
                     "packet": {"kind": "data", "payload": val}})
    for k, ins in enumerate(instrs):
        si = islot[k]
        seed.append({"ring": 0, "slot": si, "packet": {
            "kind": "instr", "op": ins["op"],
            "src_a": (vslot[ins["a"]] - si) % P,
            "src_b": (vslot[ins["b"]] - si) % P,
            "dst":   (vslot[ins["result"]] - si) % P, "payload": 0}})
    return {"config": {"rings": [{"id": 0, "period": P}], "stations": stations},
            "seed": seed, "run": [{"cycles": P, "advance": [0]}]}

def _accept(prog, P, out_slots, ref_vals, run_kind):
    """Run the program on the model; return (ok, cycles)."""
    if run_kind[0] == "loop":
        cycles = run_kind[1] * P
        prog["run"] = [{"cycles": cycles, "advance": [0]}]
        m = progmod.build(prog); m.run(prog["run"])
        got = {o: m.rings[0].slots[out_slots[o]].payload for o in ref_vals}
        return (got == ref_vals, cycles)
    maxk = len(prog["seed"]) + 2
    m = progmod.build(prog); prev = None
    for k in range(1, maxk + 1):
        m.run([{"cycles": P, "advance": [0]}])
        cur = {o: m.rings[0].slots[out_slots[o]].payload for o in ref_vals}
        if cur == ref_vals and prev == ref_vals:
            return (True, k * P)
        prev = cur
    return (False, 0)

def compile_kernel(kernel, max_candidates=None):
    """Canonical entry: the constructive scheduler (sim.scheduler) computes a
    seating with COPY relays and, when a kernel overflows one ring, places it
    across the period hierarchy. It supersedes the exhaustive backtracking placer
    below (kept as _place/_program for reference and tiny-kernel cross-checking),
    which is intractable past a handful of packets. `max_candidates` is accepted
    for backward compatibility and ignored."""
    from sim import scheduler
    return scheduler.compile_kernel(kernel)


def compile_bruteforce(kernel, max_candidates=500000):
    """The original exhaustive within-window placer (single ring, no relays).
    Retained so the constructive scheduler can be cross-checked against it on the
    small kernels both can seat."""
    P, seeded, instrs, outputs, run_kind = _items(kernel)
    values = _values(seeded, instrs)
    ref = kernel_reference(kernel)
    ref_vals = {o: ref[o] for o in outputs}
    tried = 0
    for islot, vslot in _place(P, values, instrs):
        tried += 1
        if tried > max_candidates:
            break
        prog = _program(P, seeded, instrs, islot, vslot)
        out_slots = {o: vslot[o] for o in outputs}
        ok, cycles = _accept(prog, P, out_slots, ref_vals, run_kind)
        if ok:
            prog["run"] = [{"cycles": cycles, "advance": [0]}]
            prog["outputs"] = out_slots
            return prog
    raise CompileError("no within-window seating found for kernel "
                       "(tried %d candidates)" % tried)
