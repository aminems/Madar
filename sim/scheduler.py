"""MADAR constructive scheduler. Where compiler.py seats a kernel by exhaustive
backtracking (tractable only for a handful of packets), this computes a seating
*constructively*: it issues each op in dependency order at a fixed station
(pos 0), lays packets on the ring greedily within the W=8 operand window, and
*inserts COPY relays* (same-ring XFER, to_pos==pos, which reuses the ALU offset
formula) whenever a value's producer and a consumer fall more than W slots
apart. The result is validated against the functional model exactly as compiler
does (propose, then run-and-check) -- so a returned program is correct by
construction-plus-check, and the relays are real, not hand-placed.

Single-ring here; multi-ring placement with inter-ring transfer is in
schedule_multi (built on the same primitives)."""
from sim.model import W_WIN
from sim.compiler import _items, _accept, CompileError
from sim.kernel import reference as kernel_reference
from sim import program as progmod

ALU_OPS = ("ADD", "SUB", "CMPLT")


def _operands(op):
    return (op["a"],) if op["op"] == "XFER" else (op["a"], op["b"])


def _insert_relays(seeded, instrs, maxspan):
    """Return a new op list in issue order, inserting COPY relays so that every
    value is read within `maxspan` issue positions of its freshest copy. A relay
    is {result, op:'XFER', a, b:None}; later reads are redirected to the copy.
    Ops fire in list order (earlier = higher slot = fires first per revolution),
    so issue position is a proxy for ring distance; `maxspan` is kept small
    enough that the corresponding slot gap stays inside the W=8 window.

    Keep-alive discipline: a value's freshest copy is refreshed the moment it
    would otherwise fall `maxspan` positions behind the next emit point -- so the
    relay always reads a still-reachable copy. If more than a window's worth of
    values must stay simultaneously live, no single-ring schedule keeps them all
    in reach: that is a real capacity limit, surfaced as CompileError (the caller
    demotes to a longer ring)."""
    reads = {}                                   # value -> original indices reading it
    for k, ins in enumerate(instrs):
        for nm in _operands(ins):
            reads.setdefault(nm, []).append(k)
    out = []
    fresh = {}                                   # value -> freshest copy name (once live)
    pos = {}                                     # copy name -> emit position
    for k, ins in enumerate(instrs):
        # refresh any already-live, still-needed value that would fall out of reach
        guard = 0
        while True:
            guard += 1
            if guard > 8 * (len(instrs) + 1):
                raise CompileError("relay keep-alive did not converge "
                                   "(too many simultaneously-live values for W)")
            p = len(out)
            stale = None
            for nm in fresh:
                if nm in reads and any(j >= k for j in reads[nm]):   # future read
                    if p - pos[fresh[nm]] >= maxspan:
                        stale = nm
                        break
            if stale is None:
                break
            src = fresh[stale]
            copy = "%s$r%d" % (stale, len(out))
            out.append(dict(result=copy, op="XFER", a=src, b=None))
            fresh[stale] = copy
            pos[copy] = len(out) - 1
        # anchor seed operands at their first use (placed in this op's window)
        for nm in _operands(ins):
            if nm not in fresh:
                fresh[nm] = nm
                pos[nm] = len(out)
        op = dict(result=ins["result"], op=ins["op"],
                  a=fresh.get(ins["a"], ins["a"]),
                  b=None if ins["op"] == "XFER" else fresh.get(ins["b"], ins["b"]))
        out.append(op)
        fresh[ins["result"]] = ins["result"]     # result is its own freshest copy
        pos[ins["result"]] = len(out) - 1
    return out


def _place_slots(P, seeded, ops, budget=400000):
    """Backtracking seat assignment. Ops are placed in issue order; each op's slot
    si is chosen so every already-placed operand (and an in-place result) sits in
    [si+1, si+W], then its unplaced operands and result are seated in that window.
    Forward-checked and node-budgeted, so it prunes early and stays fast. Returns
    (islot, vslot) or raises CompileError. pos=0."""
    vslot = {}
    islot = {}
    used = [False] * P
    nodes = [0]

    def seat(items, avail, undo):
        """Assign each name in `items` a distinct free slot from `avail`."""
        if not items:
            return True
        nm = items[0]
        for s in avail:
            if used[s]:
                continue
            vslot[nm] = s
            used[s] = True
            undo.append((nm, s))
            rest = [x for x in avail if x != s]
            if seat(items[1:], rest, undo):
                return True
            used[s] = False
            del vslot[nm]
            undo.pop()
        return False

    def place(k):
        if k == len(ops):
            return True
        nodes[0] += 1
        if nodes[0] > budget:
            return False
        op = ops[k]
        operands = _operands(op)
        placed = [nm for nm in operands if nm in vslot]
        res = op["result"]
        res_fixed = vslot.get(res)
        # An op's slot si must sit within W behind every already-placed operand
        # (and a fixed result), so the candidate si are the intersection of those
        # operands' windows -- usually a handful of slots, not all P. This is what
        # keeps placement near-linear instead of exponential.
        anchors = [vslot[nm] for nm in placed]
        if res_fixed is not None:
            anchors.append(res_fixed)
        if anchors:
            cand = None
            for s in anchors:
                opts = set((s - d) % P for d in range(1, W_WIN + 1))
                cand = opts if cand is None else (cand & opts)
            candidates = sorted(cand)
        else:
            candidates = range(P)
        for si in candidates:
            if used[si]:
                continue
            if not all(1 <= (vslot[nm] - si) % P <= W_WIN for nm in placed):
                continue
            if res_fixed is not None and not (1 <= (res_fixed - si) % P <= W_WIN):
                continue
            win = [(si + d) % P for d in range(1, W_WIN + 1)]
            need = []
            for nm in operands:
                if nm not in vslot and nm not in need:
                    need.append(nm)
            if res_fixed is None and res not in operands and res not in need:
                need.append(res)
            undo = []
            if not seat(need, win, undo):
                for nm, s in undo:
                    used[s] = False
                    vslot.pop(nm, None)
                continue
            used[si] = True
            islot[k] = si
            if place(k + 1):
                return True
            used[si] = False
            del islot[k]
            for nm, s in undo:
                used[s] = False
                vslot.pop(nm, None)
        return False

    if not place(0):
        raise CompileError("no constructive seating within node budget")
    return islot, vslot


def _emit(P, seeded, ops, islot, vslot):
    """Build a program.json (single ring, pos 0). Compute stations needed."""
    need_mul = any(o["op"] == "MUL" for o in ops)
    need_xfer = any(o["op"] == "XFER" for o in ops)
    stations = [{"type": "alu", "ring": 0, "pos": 0}]
    if need_mul:
        stations.append({"type": "mul", "ring": 0, "pos": 0})
    if need_xfer:
        stations.append({"type": "xfer", "ring": 0, "pos": 0,
                         "to_ring": 0, "to_pos": 0})
    seed = []
    for nm, val in seeded.items():
        seed.append({"ring": 0, "slot": vslot[nm],
                     "packet": {"kind": "data", "payload": val}})
    # placeholder data packets for produced values (results/relay copies)
    produced = set(o["result"] for o in ops) - set(seeded)
    for nm in produced:
        seed.append({"ring": 0, "slot": vslot[nm],
                     "packet": {"kind": "data", "payload": 0}})
    for k, op in enumerate(ops):
        si = islot[k]
        pk = {"kind": "instr", "op": op["op"],
              "src_a": (vslot[op["a"]] - si) % P,
              "dst": (vslot[op["result"]] - si) % P, "payload": 0}
        pk["src_b"] = 0 if op["op"] == "XFER" else (vslot[op["b"]] - si) % P
        seed.append({"ring": 0, "slot": si, "packet": pk})
    return {"config": {"rings": [{"id": 0, "period": P}], "stations": stations},
            "seed": seed, "run": [{"cycles": P, "advance": [0]}]}


# Geometric ring-period hierarchy (the memory hierarchy). A kernel that overflows
# one ring is demoted to the next-longer one; a pipeline lays its stages across it.
HIER = [16, 64, 256, 1024, 4096, 16384, 65536]


def compile_kernel(kernel, validate=True):
    """Constructively schedule a kernel. Dispatches to the multi-ring pipeline
    placer when the kernel declares stages, else seats it on one ring -- demoting
    to a longer ring in the period hierarchy if it overflows."""
    if "pipeline" in kernel:
        return _compile_pipeline(kernel, validate=validate)
    P0 = kernel["ring"]["period"]
    tiers = [P0] + [p for p in HIER if p > P0]
    last_err = None
    for P in tiers:
        k = kernel
        if P != P0:
            k = dict(kernel)
            k["ring"] = dict(kernel["ring"])
            k["ring"]["period"] = P
        try:
            prog = _compile_single(k, validate=validate)
            if P != P0:
                prog["demoted_from"] = P0
            return prog
        except CompileError as e:
            last_err = e
            if "does not reproduce" in str(e):
                raise           # a real schedule bug, not a capacity miss
            continue
    raise last_err or CompileError("no schedule found for kernel")


def _compile_single(kernel, validate=True):
    """Seat a kernel on one ring, with COPY relays as needed. The relay span is
    auto-tuned: try the fewest relays (largest span) first, tightening only if a
    seating cannot be found, so the result uses as little rotation bandwidth as
    the window allows."""
    P, seeded, instrs, outputs, run_kind = _items(kernel)
    ref = kernel_reference(kernel)
    ref_vals = {o: ref[o] for o in outputs}
    last_err = None
    for maxspan in range(W_WIN - 1, 1, -1):
        try:
            ops = _insert_relays(seeded, instrs, maxspan)
            islot, vslot = _place_slots(P, seeded, ops)
        except CompileError as e:
            last_err = e
            continue
        if len(islot) + len(vslot) > P:
            last_err = CompileError("needs %d packets > ring period %d"
                                    % (len(islot) + len(vslot), P))
            continue
        prog = _emit(P, seeded, ops, islot, vslot)
        out_slots = {o: vslot[o] for o in outputs}
        if validate:
            ok, cycles = _accept(prog, P, out_slots, ref_vals, run_kind)
            if not ok:
                last_err = CompileError("schedule (span=%d) does not reproduce "
                                        "reference %s" % (maxspan, outputs))
                continue
            prog["run"] = [{"cycles": cycles, "advance": [0]}]
        prog["outputs"] = out_slots
        prog["vslot"] = dict(vslot)
        prog["relays"] = sum(1 for o in ops if o["op"] == "XFER")
        prog["maxspan"] = maxspan
        return prog
    raise last_err or CompileError("no schedule found for kernel")


# ---------------------------------------------------------------------------
# Multi-ring pipeline placement: lay a kernel's stages across the period
# hierarchy and carry intermediates between rings by scheduled inter-ring XFER.
# The transfer is choreographed by the run plan -- settle stage i on ring i
# (others frozen), fire the cross-ring XFER in a single cycle, then settle the
# next stage -- so a value moves R(i)->R(i+1) exactly once at a known phase, the
# architectural stand-in for a cache fill. This is property (d).
# ---------------------------------------------------------------------------

SETTLE_REVS = 4                       # revolutions to settle each stage
DST_OFF_MAX = 15                      # OFF_W=4-bit dst field: XFER lands in slots 0..15


def _substage(st, carry):
    """Build a single-ring straight-line kernel for one pipeline stage; values
    imported from earlier stages enter as inputs carrying their computed value."""
    produce = st.get("produce", [])
    outs = st.get("outputs", [])
    want = list(dict.fromkeys(list(produce) + list(outs)))
    return {"ring": st["ring"], "stations": st.get("stations", ["alu"]),
            "inputs": list(st.get("inputs", []))
                      + [{"name": nm, "value": carry[nm]} for nm in st.get("import", [])],
            "consts": st.get("consts", []),
            "ops": st["ops"], "outputs": want}


def _pipeline_reference(kernel):
    carry = {}
    last_outs = []
    for st in kernel["pipeline"]:
        r = kernel_reference(_substage(st, carry))
        carry.update(r)
        if st.get("outputs"):
            last_outs = st["outputs"]
    return {o: carry[o] for o in last_outs}, last_outs


def _accept_multi(prog, ref_vals, out_ring, out_final):
    """Run a multi-ring program and check each output (at its post-run slot,
    accounting for the ring's net advance) equals the reference."""
    m = progmod.build(prog)
    m.run(prog["run"])
    got = {o: m.rings[out_ring].slots[out_final[o]].payload for o in ref_vals}
    return got == ref_vals, got


def _compile_pipeline(kernel, validate=True):
    stages = kernel["pipeline"]
    n = len(stages)
    # 1. compile each stage independently (validated in isolation), carrying
    #    computed values forward so imports get a concrete placeholder value.
    carry = {}
    sps = []
    for st in stages:
        sub = _substage(st, carry)
        p = _compile_single(sub, validate=True)
        sps.append((st, sub, p))
        carry.update(kernel_reference(sub))
    # 2. merge stage programs onto rings 0..n-1 (each stage compiled on ring 0).
    rings = [{"id": i, "period": stages[i]["ring"]["period"]} for i in range(n)]
    stations = []
    seed = []
    used = [set() for _ in range(n)]
    for i, (st, sub, p) in enumerate(sps):
        for s in p["config"]["stations"]:
            s2 = dict(s)
            s2["ring"] = i
            if "to_ring" in s2:
                s2["to_ring"] = i
            stations.append(s2)
        for e in p["seed"]:
            seed.append({"ring": i, "slot": e["slot"], "packet": e["packet"]})
            used[i].add(e["slot"])
    # 3. build the run plan and the inter-ring XFERs in lockstep, tracking each
    #    ring's net advance so every XFER station is placed where its instruction
    #    actually sits when its transfer cycle fires.
    run = []
    netadv = [0] * n
    for i in range(n):
        Pi = rings[i]["period"]
        run.append({"cycles": SETTLE_REVS * Pi, "advance": [i]})
        netadv[i] += SETTLE_REVS * Pi
        if i < n - 1:
            Psrc = Pi
            shift = netadv[i] % Psrc                 # source net shift at fire time
            imports = stages[i + 1].get("import", [])
            for nm in stages[i].get("produce", []):
                if nm not in imports:
                    continue
                su0 = sps[i][2]["vslot"][nm]          # produced slot on source ring
                su1 = sps[i + 1][2]["vslot"][nm]      # import slot on dest ring
                if su1 > DST_OFF_MAX:
                    # the dst field is OFF_W=4 bits: an inter-ring XFER can only
                    # land in the first 16 slots of the dest ring. Reaching deeper
                    # needs a same-ring relay on the dest ring (future work).
                    raise CompileError("import '%s' lands at dest slot %d > %d "
                                       "(XFER dst field width)" % (nm, su1, DST_OFF_MAX))
                sx = None
                for s in range(Psrc):
                    if s in used[i]:
                        continue
                    if 1 <= (su0 - s) % Psrc <= W_WIN:
                        sx = s
                        break
                if sx is None:
                    raise CompileError("no free XFER seat for '%s' on ring %d" % (nm, i))
                used[i].add(sx)
                stations.append({"type": "xfer", "ring": i,
                                 "pos": (sx + shift) % Psrc,
                                 "to_ring": i + 1, "to_pos": 0})
                seed.append({"ring": i, "slot": sx, "packet": {
                    "kind": "instr", "op": "XFER",
                    "src_a": (su0 - sx) % Psrc, "src_b": 0, "dst": su1, "payload": 0}})
            run.append({"cycles": 1, "advance": [i, i + 1]})
            netadv[i] += 1
            netadv[i + 1] += 1
    prog = {"config": {"rings": rings, "stations": stations},
            "seed": seed, "run": run}
    # 4. outputs live on the last stage's ring, at their slot shifted by net advance
    ref_vals, out_names = _pipeline_reference(kernel)
    out_ring = n - 1
    Pl = rings[out_ring]["period"]
    out_final = {o: (sps[out_ring][2]["vslot"][o] + netadv[out_ring]) % Pl
                 for o in out_names}
    if validate:
        ok, got = _accept_multi(prog, ref_vals, out_ring, out_final)
        if not ok:
            raise CompileError("pipeline does not reproduce reference: got %s want %s"
                               % (got, ref_vals))
    prog["outputs"] = out_final
    prog["out_ring"] = out_ring
    prog["transfers"] = sum(1 for st in stations
                            if st["type"] == "xfer" and st["to_ring"] != st["ring"])
    prog["relays"] = sum(1 for st in stations
                         if st["type"] == "xfer" and st["to_ring"] == st["ring"])
    return prog
