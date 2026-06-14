"""MADAR as an AI accelerator: build the AI primitives from MADAR kernels and
price them.

The atom of all dense AI compute is the multiply-accumulate. MADAR already
compiles a single MAC (kernels/mac.json, crossover 31 pJ/instr) and a MAC *loop*
(kernels/firtap.json, 16 pJ/instr -- the dataflow that wins). This module builds
the next rung -- a real dot product over distinct operands, and the matrix-vector
and GEMM tiles built from it -- and runs them through the constructive scheduler
and the energy model.

The dot product is expressed as a *sequential MAC accumulation*
    s_i = s_{i-1} + a_i * b_i
so each ADD consumes the running sum (just reproduced) and one fresh product:
the two operands are the two most-recent results, which the existing constructive
placer seats with no relays. (A flat reduction tree -- p_i = a_i*b_i for all i,
then a balanced add tree -- does NOT seat: its independent products scatter and
the adds cannot reach two of them in one W=8 window. Sequential accumulation is
what keeps the reduction within scope.)

Honest finding (see report()): a dot product seated *flat* this way is a small
computation (2N ops) carrying many packets (~6N), so it demotes to an oversized
ring and loses to rotation as N grows -- the same lesson as the paper's
polynomial chains. The MAC *loop* (firtap) is the dataflow that wins; streaming
distinct taps through a sized MAC loop is the concrete next mechanism (docs/AI.md)."""
from sim import scheduler, program as pm, energy as en, energy_costs as ec
from sim.compiler import CompileError


def dot_kernel(a, b, P=16):
    """y = sum_i a_i*b_i as a sequential MAC chain (one ring, ALU+MUL station)."""
    if len(a) != len(b):
        raise ValueError("dot: length mismatch")
    seeds = [{"name": "z", "value": 0}]
    for i, (ai, bi) in enumerate(zip(a, b)):
        seeds.append({"name": "a%d" % i, "value": ai})
        seeds.append({"name": "b%d" % i, "value": bi})
    ops = []
    acc = "z"
    for i in range(len(a)):
        ops.append({"id": "p%d" % i, "op": "MUL", "a": "a%d" % i, "b": "b%d" % i})
        ops.append({"id": "s%d" % i, "op": "ADD", "a": acc, "b": "p%d" % i})
        acc = "s%d" % i
    return {"ring": {"period": P}, "stations": ["alu", "mul"],
            "inputs": seeds, "consts": [], "ops": ops, "outputs": [acc]}


def run_kernel(kernel):
    """Compile a straight-line kernel, run the emitted program on the model, and
    return (prog, {output: value})."""
    prog = scheduler.compile_kernel(kernel)
    m = pm.build(prog)
    m.run(prog["run"])
    orr = prog.get("out_ring", 0)
    got = {o: m.rings[orr].slots[sl].payload for o, sl in prog["outputs"].items()}
    return prog, got


def dot(a, b):
    """Compile+run a dot product; return its scalar value."""
    k = dot_kernel(a, b)
    _, got = run_kernel(k)
    return got[k["outputs"][0]]


def matvec(W, x):
    """y = W x, output-stationary: each output row is one dot product."""
    return [dot(row, x) for row in W]


def gemm_tile(A, B):
    """C = A B for small dense tiles; C_ij = dot(row_i(A), col_j(B))."""
    n = len(B)
    cols = [[B[r][j] for r in range(n)] for j in range(len(B[0]))]
    return [[dot(A[i], cols[j]) for j in range(len(cols))] for i in range(len(A))]


# ---------------------------------------------------------------------------
# Streaming MAC: the dataflow that keeps the win. The compute ring R0 is small
# and CONSTANT-size; weights stream on a longer ring R1, each paired with its own
# XFER that lands it on R0, where one reused MAC accumulates. Distinct tap per
# pass, R0 never grows with N -- the opposite of the flat unroll's ~6N packets.
# Geometries below were found by simulation search (one R0 revolution per tap).
# ---------------------------------------------------------------------------

def stream_sum(weights):
    """acc = sum_i w_i, weights streamed onto a period-4 accumulator ring."""
    Pa, S = 4, 4
    add, acc, w, land = 1, 0, 2, 3
    return _stream_build(weights, Pa, S, acc=acc, wslot=w, land=land,
                         insts=[("alu", "ADD", add, acc, w, acc)], const=None)


def stream_mac(weights, c):
    """acc = sum_i (w_i * c): a streaming multiply-accumulate (MUL then ADD per
    tap) over distinct streamed weights and a resident constant c, on a period-8
    compute ring that does not grow with N."""
    Pa, S = 8, 8
    add, acc, w, prod, cs, mul, land = 1, 0, 2, 3, 4, 5, 3
    return _stream_build(weights, Pa, S, acc=acc, wslot=w, land=land, const=(cs, c),
                         insts=[("mul", "MUL", mul, w, cs, prod),
                                ("alu", "ADD", add, acc, prod, acc)])


def stream_dot(a, b):
    """acc = sum_i a_i*b_i: the full inner product with BOTH operands streamed.
    Two stream rings -- R1 weights, R2 activations -- each value paired with its
    own XFER landing on R0, where one MUL+ADD accumulates per pass. R0 is a fixed
    period-8 ring regardless of N: a complete dot-product / GEMM-row primitive
    that holds the streaming win. Geometry found by simulation search, locked."""
    if len(a) != len(b):
        raise ValueError("stream_dot: length mismatch")
    Pa, S = 8, 8
    acc, wl, xl, prod, mul, add, landw, landx = 0, 1, 2, 3, 5, 4, 2, 3
    N, Pw = len(a), len(a) * 8
    seed = [{"ring": 0, "slot": s, "packet": {"kind": "data", "payload": 0}}
            for s in (acc, wl, xl, prod)]
    seed.append({"ring": 0, "slot": mul, "packet": {"kind": "instr", "op": "MUL",
        "src_a": (wl - mul) % Pa, "src_b": (xl - mul) % Pa,
        "dst": (prod - mul) % Pa, "payload": 0}})
    seed.append({"ring": 0, "slot": add, "packet": {"kind": "instr", "op": "ADD",
        "src_a": (acc - add) % Pa, "src_b": (prod - add) % Pa,
        "dst": (acc - add) % Pa, "payload": 0}})
    for rid, vals, land in ((1, a, landw), (2, b, landx)):
        for i, v in enumerate(vals):
            s = i * S
            seed.append({"ring": rid, "slot": s,
                         "packet": {"kind": "data", "payload": v}})
            seed.append({"ring": rid, "slot": (s - 1) % Pw, "packet": {"kind": "instr",
                "op": "XFER", "src_a": 1, "src_b": 0, "dst": land, "payload": 0}})
    return {"config": {"rings": [{"id": 0, "period": Pa},
                                 {"id": 1, "period": Pw}, {"id": 2, "period": Pw}],
            "stations": [{"type": "mul", "ring": 0, "pos": 0},
                         {"type": "alu", "ring": 0, "pos": 0},
                         {"type": "xfer", "ring": 1, "pos": 0, "to_ring": 0, "to_pos": 0},
                         {"type": "xfer", "ring": 2, "pos": 0, "to_ring": 0, "to_pos": 0}]},
            "seed": seed, "run": [{"cycles": Pw, "advance": [0, 1, 2]}], "accslot": acc}


def stream_matvec(W, x):
    """y = W x via streaming inner products -- one streamed dot product per row."""
    return [run_stream(stream_dot(row, x)) for row in W]


def gemm(A, B):
    """C = A B, the full matmul, on the model: each C[i][j] is one streamed inner
    product of row i of A with column j of B."""
    cols = [[B[r][j] for r in range(len(B))] for j in range(len(B[0]))]
    return [[run_stream(stream_dot(A[i], col)) for col in cols] for i in range(len(A))]


# A fused 2-wide tile: two outputs (y0=W0.x, y1=W1.x) sharing ONE streamed
# activation x -- proof that a streamed operand is reused IN HARDWARE (the two
# MULs land adjacent, reading the same rotating x one cycle apart). Geometry
# found by simulation search, then locked. But tile2_per_mac_pj() shows the
# catch: fusing outputs onto a wider compute ring costs more rotation than the
# stream reuse saves, so it LOSES to two separate streaming dots. Reuse on MADAR
# belongs in the period hierarchy (promote the shared operand to a fast ring),
# not in a wider compute ring.
_TILE2 = (5, 9, 6, 14, 10, 13, 1, 11, 12, 4, 2)  # acc0 acc1 wl0 wl1 xl prod0 prod1 mul0 mul1 add0 add1
_TILE2_LANDS = (11, 7, 15)                        # x (shared), w0, w1


def tile2(W0, W1, x, Pa=16):
    """Two dot products with x streamed ONCE and reused by both MAC units."""
    acc0, acc1, wl0, wl1, xl, prod0, prod1, mul0, mul1, add0, add1 = _TILE2
    lx, lw0, lw1 = _TILE2_LANDS
    K, S = len(x), Pa
    Pw = K * S
    d = lambda a, b: (a - b) % Pa
    seed = [{"ring": 0, "slot": s, "packet": {"kind": "data", "payload": 0}}
            for s in (acc0, acc1, wl0, wl1, xl, prod0, prod1)]
    for islot, a, b, dd in ((mul0, wl0, xl, prod0), (mul1, wl1, xl, prod1)):
        seed.append({"ring": 0, "slot": islot, "packet": {"kind": "instr", "op": "MUL",
            "src_a": d(a, islot), "src_b": d(b, islot), "dst": d(dd, islot), "payload": 0}})
    for islot, a, b, dd in ((add0, acc0, prod0, acc0), (add1, acc1, prod1, acc1)):
        seed.append({"ring": 0, "slot": islot, "packet": {"kind": "instr", "op": "ADD",
            "src_a": d(a, islot), "src_b": d(b, islot), "dst": d(dd, islot), "payload": 0}})
    for rid, vals, land in ((1, x, lx), (2, W0, lw0), (3, W1, lw1)):
        for i, v in enumerate(vals):
            s = i * S
            seed.append({"ring": rid, "slot": s, "packet": {"kind": "data", "payload": v}})
            seed.append({"ring": rid, "slot": (s - 1) % Pw, "packet": {"kind": "instr",
                "op": "XFER", "src_a": 1, "src_b": 0, "dst": land, "payload": 0}})
    rings = [{"id": 0, "period": Pa}] + [{"id": r, "period": Pw} for r in (1, 2, 3)]
    stations = [{"type": "mul", "ring": 0, "pos": 0}, {"type": "alu", "ring": 0, "pos": 0}] + \
               [{"type": "xfer", "ring": r, "pos": 0, "to_ring": 0, "to_pos": 0} for r in (1, 2, 3)]
    return {"config": {"rings": rings, "stations": stations}, "seed": seed,
            "run": [{"cycles": Pw, "advance": [0, 1, 2, 3]}], "outs": (acc0, acc1)}


def run_tile2(prog):
    m = pm.build(prog)
    m.run(prog["run"])
    return tuple(m.rings[0].slots[s].payload for s in prog["outs"])


def tile2_per_mac_pj():
    """Per-MAC energy of the fused 2-wide tile (Pa=16, 11 occupied packets, 2 MACs
    per pass; x shared so its stream is read once for both). Compare against
    stream_per_tap_pj('dot') for one streaming dot."""
    rot = 11 * 16 * ec.E_WORD_SHIFT_OPT
    compute = 2 * (ec.E_MUL + ec.E_ALU)
    stream = (2 + 2 + 2) * ec.E_SRAM_ACCESS      # x shared + w0 + w1
    land = 3 * ec.E_WORD_SHIFT_OPT
    return (rot + compute + stream + land) / 2.0


# Per-ring energy tier by size, faithful to the thesis (small rings are real
# shift registers -- cheap flop-to-flop word transfer; large rings are
# single-port SRAM read by a rotating pointer -- pricier). This is the lever for
# reuse: a SHARED operand made cheap to re-read by promoting it to a small ring.
E_READ_FAST = ec.E_WORD_SHIFT_OPT     # ~1 pJ  -- re-read from a small fast ring
E_READ_SLOW = ec.E_SRAM_ACCESS        # ~10 pJ -- read from a large SRAM buffer


def reuse_matvec_pj(M, K):
    """Per-MAC energy of an M-output, length-K matrix-vector under three reuse
    dataflows -- all computing the SAME result (ai.stream_matvec, validated),
    differing only in WHERE the shared activation lives:
      naive     : each output re-streams x from the big SRAM buffer (~10 pJ/read).
      fused     : outputs fused onto one wide compute ring (ai.tile2, measured).
      hierarchy : x PROMOTED once to a small fast ring, then re-read there at
                  ~1 pJ by each output -- separate small compute rings, so no
                  fused-ring rotation penalty (property d). The promotion is the
                  already-validated big->small inter-ring XFER; here we price it.
    Returns {dataflow: pJ_per_MAC}."""
    macs = M * K
    base = (6 * 8 * ec.E_WORD_SHIFT_OPT          # small compute ring, 1 MAC/rev
            + ec.E_MUL + ec.E_ALU                # multiply-add
            + 2 * E_READ_SLOW                    # each weight: its own, from SRAM
            + 2 * ec.E_WORD_SHIFT_OPT)           # landings
    naive_x = 2 * E_READ_SLOW                    # x re-read from SRAM every MAC
    promote = K * (E_READ_SLOW + E_READ_FAST) / macs   # x read once into the fast ring
    hier_x = 2 * E_READ_FAST + promote           # cheap re-read + amortized promotion
    return {"naive": base + naive_x, "fused": tile2_per_mac_pj(),
            "hierarchy": base + hier_x}


def _stream_build(weights, Pa, S, acc, wslot, land, insts, const):
    """Assemble a streaming program: R0 (period Pa) carries the accumulator, the
    landing slot, an optional resident constant, and the MAC instruction(s); R1
    (period N*S) carries each weight paired with an XFER that lands it on R0."""
    N = len(weights)
    Pw = N * S
    stations = [{"type": "xfer", "ring": 1, "pos": 0, "to_ring": 0, "to_pos": 0}]
    have = {st[0] for st in insts}
    for t in ("alu", "mul"):
        if t in have:
            stations.append({"type": t, "ring": 0, "pos": 0})
    seed = [{"ring": 0, "slot": acc, "packet": {"kind": "data", "payload": 0}},
            {"ring": 0, "slot": wslot, "packet": {"kind": "data", "payload": 0}}]
    if const is not None:
        cs, cval = const
        seed.append({"ring": 0, "slot": cs, "packet": {"kind": "data", "payload": cval}})
    for st, op, islot, a, b, d in insts:
        if op != "ADD":  # MUL writes a product slot we must seed as data first
            seed.append({"ring": 0, "slot": d, "packet": {"kind": "data", "payload": 0}})
    for st, op, islot, a, b, d in insts:
        seed.append({"ring": 0, "slot": islot, "packet": {"kind": "instr", "op": op,
            "src_a": (a - islot) % Pa, "src_b": (b - islot) % Pa,
            "dst": (d - islot) % Pa, "payload": 0}})
    for i, wt in enumerate(weights):
        ws = i * S
        seed.append({"ring": 1, "slot": ws, "packet": {"kind": "data", "payload": wt}})
        seed.append({"ring": 1, "slot": (ws - 1) % Pw, "packet": {"kind": "instr",
            "op": "XFER", "src_a": 1, "src_b": 0, "dst": land, "payload": 0}})
    return {"config": {"rings": [{"id": 0, "period": Pa}, {"id": 1, "period": Pw}],
                       "stations": stations},
            "seed": seed, "run": [{"cycles": Pw, "advance": [0, 1]}], "accslot": acc}


def run_stream(prog):
    """Run a streaming program on the model; return the accumulator value."""
    m = pm.build(prog)
    m.run(prog["run"])
    return m.rings[0].slots[prog["accslot"]].payload


def stream_per_tap_pj(kind="mac", gated=True):
    """Honest per-tap energy of a streaming kernel, CONSTANT in N (R0 is fixed).
    R0 is a real shift register (E_WORD_SHIFT_OPT/slot; clock-gated = only the
    occupied packets toggle). Each stream ring is an SRAM rotating pointer
    accessed ON DEMAND -- one value + its xfer read per tap, not every cycle (the
    run plan rotates the stream rings continuously; pricing only the consumed
    accesses is the faithful cost, per the paper's clock-gating caveat). Returns
    (pj, parts).
        kind: 'sum' (acc+=w_i), 'mac' (acc+=w_i*c), 'dot' (acc+=w_i*x_i)."""
    #                 Pa  occ muls adds streams lands
    cfg = {"sum":     (4,  3,  0,   1,   1,      1),   # acc, w, ADD
           "mac":     (8,  6,  1,   1,   1,      1),   # acc, w, prod, c, MUL, ADD
           "dot":     (8,  6,  1,   1,   2,      2)}[kind]  # acc, w, x, prod, MUL, ADD
    Pa, occ, muls, adds, streams, lands = cfg
    slots = occ if gated else Pa
    rot = slots * Pa * ec.E_WORD_SHIFT_OPT           # one R0 revolution / tap
    compute = muls * ec.E_MUL + adds * ec.E_ALU
    stream = streams * 2 * ec.E_SRAM_ACCESS          # each stream: value + xfer read
    land = lands * ec.E_WORD_SHIFT_OPT               # XFER write(s) into R0
    total = rot + compute + stream + land
    return total, {"R0_rotation": rot, "compute": compute,
                   "stream": stream, "land": land}


def _next_pow2(n):
    p = 1
    while p < n:
        p *= 2
    return p


def fair_crossover(kernel):
    """Lowest crossover (pJ/instr) across the three rotation pricings, with the
    ring right-sized to the kernel -- the next power of two >= packet count, the
    paper's 'ring sized to the kernel' rule (the demotion ladder is geometric x4,
    so a kernel can land on a ring up to 2x larger than it needs). Returns
    (best_ring, crossover)."""
    prog = scheduler.compile_kernel(kernel)
    landed = prog["config"]["rings"][0]["period"]
    cands = [(landed, prog)]
    rp = _next_pow2(len(prog["seed"]))
    if rp < landed:
        kk = dict(kernel)
        kk["ring"] = dict(kernel["ring"])
        kk["ring"]["period"] = rp
        try:
            cands.append((rp, scheduler._compile_single(kk)))
        except CompileError:
            pass
    best_P = best = None
    for Pi, pi in cands:
        x = min(en.crossover(en.activity(pi, t), t, kernel)
                for t in ("shift_opt", "shift", "sram"))
        if best is None or x < best:
            best_P, best = Pi, x
    return best_P, best


def dot_energy(a, b):
    """Right-sized best crossover for a dot product.
    Returns (best_ring, crossover, landed_ring, packets)."""
    k = dot_kernel(a, b)
    prog = scheduler.compile_kernel(k)
    landed = prog["config"]["rings"][0]["period"]
    best_P, best = fair_crossover(k)
    return best_P, best, landed, len(prog["seed"])


def report():
    L = []
    L.append("=== MADAR as an AI accelerator: primitives + energy ===")
    L.append("baseline = %g pJ/instr (Horowitz in-order); below it, MADAR wins.\n"
             % ec.E_PER_INSTR_CENTRAL)

    L.append("-- correctness: AI primitives compile and run on the model --")
    W = [[1, 2, 3], [4, 5, 6], [0, 1, 0]]
    x = [2, 1, 3]
    yref = [sum(W[i][j] * x[j] for j in range(3)) for i in range(3)]
    ymad = matvec(W, x)
    L.append("  matrix-vector 3x3:  y=%s  %s" % (ymad, "OK" if ymad == yref else "MISMATCH"))
    A, B = [[1, 2], [3, 4]], [[5, 6], [7, 8]]
    cref = [[sum(A[i][k] * B[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    cmad = gemm_tile(A, B)
    L.append("  GEMM tile 2x2@2x2:  C=%s  %s" % (cmad, "OK" if cmad == cref else "MISMATCH"))

    L.append("\n-- energy: a flat dot product loses to rotation as it grows --")
    L.append("  (ring right-sized to the kernel, lowest of the three rotation pricings)")
    L.append("  %-5s %-10s %-9s %-10s %s" % ("N", "ring", "packets", "crossover", "vs 70 pJ"))
    for N in (2, 4, 8, 12):
        a = [i + 1 for i in range(N)]
        b = [2] * N
        ring, best, landed, pkts = dot_energy(a, b)
        verdict = "WINS" if best < ec.E_PER_INSTR_CENTRAL else "loses"
        L.append("  %-5d %-10d %-9d %-10.1f %s" % (N, ring, pkts, best, verdict))
    L.append("  Only the tiny N=2 fits a 16-ring and wins; ~6N packets of a 2N-op")
    L.append("  reduction force a bigger ring, so rotation dominates and the crossover")
    L.append("  climbs with N -- the paper's small-compute-in-a-big-ring lesson.")

    L.append("\n-- the dataflow that wins: a sized MAC loop, not a flat unroll --")
    from sim import kernel as kmod
    fk = kmod.load("sim/kernels/firtap.json")
    fP, fx = fair_crossover(fk)
    L.append("  firtap MAC loop (acc += c*x, ring=%d): crossover=%.1f pJ/instr -> WINS"
             % (fP, fx))
    L.append("  The loop body is a few packets on a small ring, re-executed once")
    L.append("  per revolution -- the efficient AI form, but firtap reuses constants.")

    L.append("\n-- streaming MAC: distinct taps through a constant-size ring --")
    L.append("  operands stream on longer rings; each is XFER'd onto a small fixed")
    L.append("  R0 where one reused MAC accumulates. R0 never grows with N.")
    W3, xv = [[1, 2, 3], [4, 5, 6], [0, 1, 0]], [2, 1, 3]
    yref = [sum(W3[i][j] * xv[j] for j in range(3)) for i in range(3)]
    ymad = stream_matvec(W3, xv)
    L.append("  full inner product (BOTH operands streamed) -- matrix-vector 3x3:")
    L.append("    y=%s  %s" % (ymad, "OK" if ymad == yref else "MISMATCH"))
    L.append("  correctness + constant R0 across N (R0 stays 4/8):")
    L.append("  %-5s %-6s %-6s %-6s" % ("N", "sum", "mac", "dot"))
    for N in (4, 8, 16, 32):
        w = [i + 1 for i in range(N)]
        ok = lambda c: "ok" if c else "X"
        L.append("  %-5d %-6s %-6s %-6s" % (N,
                 ok(run_stream(stream_sum(w)) == sum(w)),
                 ok(run_stream(stream_mac(w, 3)) == 3 * sum(w)),
                 ok(run_stream(stream_dot(w, [2] * N)) == 2 * sum(w))))
    L.append("  per-tap energy (CONSTANT in N; clock-gated R0 shift + on-demand streams):")
    for kind, lbl in (("sum", "streaming sum  acc+=w  "),
                      ("mac", "streaming MAC  acc+=w*c"),
                      ("dot", "streaming dot  acc+=w*x")):
        pj, pb = stream_per_tap_pj(kind)
        L.append("    %s  %5.1f pJ/tap  (R0 %.0f + cmp %.1f + stream %.0f + land %.0f)"
                 % (lbl, pj, pb["R0_rotation"], pb["compute"], pb["stream"], pb["land"]))
    L.append("  the full inner product is ~103 pJ/tap vs ~210 pJ in-order (2 loads +")
    L.append("  MAC): wins ~2x, and -- unlike the flat dot, whose crossover climbs with")
    L.append("  N -- the per-tap cost is flat because R0 never grows. See docs/AI.md.")

    L.append("\n-- tiled GEMM: a full matmul, and an honest reuse finding --")
    A, B = [[1, 2], [3, 4]], [[5, 6], [7, 8]]
    cref = [[sum(A[i][k] * B[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    L.append("  GEMM C=A B (streamed inner product per element): %s  %s"
             % (gemm(A, B), "OK" if gemm(A, B) == cref else "MISMATCH"))
    y = run_tile2(tile2([2, 3, 4], [1, 5, 2], [5, 6, 7]))
    L.append("  fused 2-wide tile (x streamed ONCE, feeds both MACs): y=%s  %s"
             % (list(y), "OK" if y == (56, 49) else "MISMATCH"))
    dpm, _ = stream_per_tap_pj("dot")
    tpm = tile2_per_mac_pj()
    L.append("  per-MAC energy: two separate dots %.1f pJ  vs  fused 2-wide tile %.1f pJ"
             % (dpm, tpm))
    L.append("  => fusing outputs reuses the activation (stream 40->30/MAC) but the")
    L.append("     wider ring rotates more (48->88/MAC): net %s. Output-fusion is NOT"
             % ("WORSE" if tpm > dpm else "better"))
    L.append("     the MADAR way to reuse -- the streaming dot is near-optimal. Reuse")
    L.append("     belongs in the period hierarchy: promote a shared operand to a fast")
    L.append("     ring so its consumers re-read it at ~1 pJ, not ~10 pJ (property d).")
    rm = reuse_matvec_pj(8, 8)
    L.append("  hierarchy reuse, per-MAC for an 8x8 matvec (x promoted to a fast ring,")
    L.append("  re-read by each output): naive %.1f  >  HIERARCHY %.1f  pJ/MAC  (fused %.1f)"
             % (rm["naive"], rm["hierarchy"], rm["fused"]))
    L.append("  -- the only dataflow that beats a lone streaming dot (102.6): small")
    L.append("     compute rings kept separate + one cheap shared fast ring. The AI")
    L.append("     reuse story routes through MADAR's sharpest novelty, property (d).")
    return "\n".join(L)


def main():
    print(report())


if __name__ == "__main__":
    main()
