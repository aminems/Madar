"""Self-checking tests for the MADAR AI primitives (no pytest on the box).
Asserts the dot product, matrix-vector, and GEMM tile compile and run to their
reference values, and that the energy regime is the one the finding claims:
the sized MAC loop wins, the flat dot product demotes and loses as it grows."""
import sys
from sim import ai, energy_costs as ec
from sim import kernel as kmod

_fails = 0
def expect(name, cond):
    global _fails
    if cond is not True:
        print("FAIL", name); _fails += 1
    else:
        print("pass", name)


def t_dot():
    # 1*5 + 2*6 + 3*7 + 4*8 = 70
    expect("dot4 == 70", ai.dot([1, 2, 3, 4], [5, 6, 7, 8]) == 70)
    expect("dot (single tap) == 42", ai.dot([6], [7]) == 42)
    expect("dot8 == 2*sum(1..8)=72", ai.dot([i + 1 for i in range(8)], [2] * 8) == 72)


def t_dot4_kernel_file():
    """The static cross-checkable kernel reproduces its reference."""
    k = kmod.load("sim/kernels/dot4.json")
    _, got = ai.run_kernel(k)
    expect("dot4.json -> 70", got[k["outputs"][0]] == kmod.reference(k)["s3"] == 70)


def t_matvec():
    W = [[1, 2, 3], [4, 5, 6], [0, 1, 0]]
    x = [2, 1, 3]
    ref = [sum(W[i][j] * x[j] for j in range(3)) for i in range(3)]
    expect("matvec 3x3 == ref %s" % ref, ai.matvec(W, x) == ref)


def t_gemm_tile():
    A, B = [[1, 2], [3, 4]], [[5, 6], [7, 8]]
    ref = [[sum(A[i][k] * B[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    expect("gemm 2x2 == [[19,22],[43,50]]", ai.gemm_tile(A, B) == ref)


def t_energy_regime():
    # The sized MAC loop (firtap), ring right-sized, wins -- the paper's 16 pJ.
    _, fx = ai.fair_crossover(kmod.load("sim/kernels/firtap.json"))
    expect("firtap MAC loop wins (<70)", fx < ec.E_PER_INSTR_CENTRAL)
    # The flat dot product needs a ring bigger than 16 and loses as it grows.
    ring8, best8, _, _ = ai.dot_energy([i + 1 for i in range(8)], [2] * 8)
    expect("dot8 on a ring bigger than 16", ring8 > 16)
    expect("dot8 flat loses (>70)", best8 > ec.E_PER_INSTR_CENTRAL)


def t_streaming():
    # Distinct streamed taps accumulate on a constant-size compute ring.
    for N in (3, 8, 16, 32):
        w = [i + 1 for i in range(N)]
        expect("stream_sum N=%d == %d" % (N, sum(w)), ai.run_stream(ai.stream_sum(w)) == sum(w))
        expect("stream_mac N=%d == %d" % (N, 3 * sum(w)),
               ai.run_stream(ai.stream_mac(w, 3)) == 3 * sum(w))
        # full inner product: BOTH operands streamed.
        x = [N - i for i in range(N)]
        want = sum(w[i] * x[i] for i in range(N))
        expect("stream_dot N=%d == %d" % (N, want), ai.run_stream(ai.stream_dot(w, x)) == want)
    # matrix-vector via streaming inner products.
    W, xv = [[1, 2, 3], [4, 5, 6], [0, 1, 0]], [2, 1, 3]
    ref = [sum(W[i][j] * xv[j] for j in range(3)) for i in range(3)]
    expect("stream_matvec 3x3 == %s" % ref, ai.stream_matvec(W, xv) == ref)
    # R0 (the compute ring) does not grow with N -- the whole point.
    r = lambda p: p["config"]["rings"][0]["period"]
    expect("stream MAC R0 constant in N",
           r(ai.stream_mac([1, 2], 3)) == r(ai.stream_mac(list(range(32)), 3)) == 8)
    expect("stream dot R0 constant in N",
           r(ai.stream_dot([1, 2], [3, 4])) == r(ai.stream_dot(list(range(32)), list(range(32)))) == 8)
    # Per-tap energy is finite and the streaming sum beats a 70 pJ in-order op.
    expect("streaming sum < 70 pJ/tap", ai.stream_per_tap_pj("sum")[0] < ec.E_PER_INSTR_CENTRAL)
    expect("streaming dot < 210 pJ/tap (2 loads+MAC)", ai.stream_per_tap_pj("dot")[0] < 210)


def t_tiled():
    # Full matmul on the model via streamed inner products.
    A, B = [[1, 2, 3], [4, 5, 6]], [[7, 8], [9, 10], [11, 12]]
    ref = [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(2)] for i in range(2)]
    expect("gemm 2x3 @ 3x2 == %s" % ref, ai.gemm(A, B) == ref)
    # Fused 2-wide tile: x streamed once, reused by both MAC units; scales over K.
    import random
    random.seed(3)
    for K in (2, 4, 8, 16):
        W0 = [random.randint(1, 9) for _ in range(K)]
        W1 = [random.randint(1, 9) for _ in range(K)]
        x = [random.randint(1, 9) for _ in range(K)]
        want = (sum(W0[i] * x[i] for i in range(K)), sum(W1[i] * x[i] for i in range(K)))
        expect("tile2 K=%d shares x, == %s" % (K, list(want)),
               ai.run_tile2(ai.tile2(W0, W1, x)) == want)
    # The honest finding: fusing outputs LOSES to two separate streaming dots
    # (the wider compute ring rotates more than the stream reuse saves).
    expect("fused tile loses to separate dots",
           ai.tile2_per_mac_pj() > ai.stream_per_tap_pj("dot")[0])
    # Hierarchy reuse (promote shared operand to a fast ring) is the dataflow that
    # wins: cheaper than a lone streaming dot, and cheaper than naive and fused.
    rm = ai.reuse_matvec_pj(8, 8)
    dot = ai.stream_per_tap_pj("dot")[0]
    expect("hierarchy reuse beats naive", rm["hierarchy"] < rm["naive"])
    expect("hierarchy reuse beats fused", rm["hierarchy"] < rm["fused"])
    expect("hierarchy reuse beats a lone streaming dot", rm["hierarchy"] < dot)


def t_stream_programs():
    """The saved cross-checkable streaming programs reproduce their references."""
    import json
    with open("sim/programs/stream_mac.json") as f:
        expect("stream_mac.json acc == 30", ai.run_stream(json.load(f)) == 30)  # (1+2+3+4)*3
    with open("sim/programs/stream_dot.json") as f:
        expect("stream_dot.json acc == 20", ai.run_stream(json.load(f)) == 20)  # [1,2,3,4].[4,3,2,1]
    with open("sim/programs/tile2.json") as f:
        expect("tile2.json == (56,49)", ai.run_tile2(json.load(f)) == (56, 49))


if __name__ == "__main__":
    t_dot()
    t_dot4_kernel_file()
    t_matvec()
    t_gemm_tile()
    t_energy_regime()
    t_streaming()
    t_tiled()
    t_stream_programs()
    print("check_ai:", "OK" if _fails == 0 else ("%d FAILS" % _fails))
    sys.exit(1 if _fails else 0)
