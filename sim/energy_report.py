"""Print the MADAR energy comparison: per-kernel energy (rotation + compute +
relay), the in-order baseline, and the crossover pJ/instruction for three ring
pricings -- shift-register (conservative reg-file proxy), shift_opt (realistic
flop toggle), and SRAM -- a right-sized-ring row, a relay/capacity tradeoff
sweep, and a plain-language summary. Honest by design: it names the regimes
where MADAR loses, and now prices the COPY relays the scheduler inserts."""
import sys
from sim import kernel as kmod, compiler, scheduler, energy as en, energy_costs as ec

KERNELS = ["mac", "poly", "sumloop", "firtap", "horner", "chainsum"]
TIERS = [("shift", "shift-register (cons.)"),
         ("shift_opt", "shift-register (flop)"),
         ("sram", "SRAM rotating-ptr")]

def _next_pow2(n):
    p = 1
    while p < n:
        p *= 2
    return p

def _chain(n, P):
    """A length-n dependence chain reading a shared constant every step -- the
    canonical relay generator: the constant must be relayed along the snake."""
    k = {"ring": {"period": P}, "stations": ["alu"],
         "inputs": [{"name": "x", "value": 1}], "consts": [{"name": "c", "value": 1}],
         "ops": [], "outputs": []}
    prev = "x"
    for i in range(n):
        k["ops"].append({"id": "t%d" % i, "op": "ADD", "a": prev, "b": "c"})
        prev = "t%d" % i
    k["outputs"] = [prev]
    return k

def report():
    L = []
    L.append("=== MADAR energy model (all energies in pJ) ===")
    L.append("Assumptions (Horowitz ISSCC 2014, 45nm, 64-bit; see energy_costs.py):")
    L.append("  E_ALU=%g  E_MUL=%g  E_WORD_SHIFT=%g (cons.)  E_WORD_SHIFT_OPT=%g (flop)  E_SRAM=%g"
             % (ec.E_ALU, ec.E_MUL, ec.E_WORD_SHIFT, ec.E_WORD_SHIFT_OPT, ec.E_SRAM_ACCESS))
    L.append("  baseline = instr_count x pJ/instr; central=%g (Horowitz in-order), sweep=%s"
             % (ec.E_PER_INSTR_CENTRAL, ec.E_PER_INSTR_RANGE))
    L.append("  relay/transfer (XFER) priced as one 64-bit word move at the tier rate")
    L.append("")
    summary = []
    for name in KERNELS:
        k = kmod.load("sim/kernels/%s.json" % name)
        prog = compiler.compile_kernel(k)
        P = prog["config"]["rings"][0]["period"]
        n = en.instr_count(k)
        base = en.baseline_energy(k, ec.E_PER_INSTR_CENTRAL)
        L.append("%-9s P=%d  instrs=%d  relays=%d  baseline@%gpJ=%.0f pJ"
                 % (name, P, n, prog.get("relays", 0), ec.E_PER_INSTR_CENTRAL, base))
        best_xo = None
        for tier, label in TIERS:
            a = en.activity(prog, tier)
            m = en.madar_energy(a, tier)
            xo = en.crossover(a, tier, k)
            verdict = "WIN " if ec.E_PER_INSTR_CENTRAL > xo else "lose"
            L.append("    %-22s MADAR=%8.0f (rot %.0f + cmp %.1f + rly %.0f)  M/B=%5.2f  "
                     "crossover=%7.1f pJ/instr  %s"
                     % (label, m["total"], m["rotation"], m["compute"], m["relay"],
                        m["total"] / base, xo, verdict))
            best_xo = xo if best_xo is None else min(best_xo, xo)
        rp = _next_pow2(len(prog["seed"]))
        if rp < P:
            kk = dict(k); kk["ring"] = dict(k["ring"]); kk["ring"]["period"] = rp
            try:
                pr = compiler.compile_kernel(kk)
                a = en.activity(pr, "shift_opt")
                xo = en.crossover(a, "shift_opt", kk)
                L.append("    right-sized P=%d (flop): crossover=%.1f pJ/instr "
                         "relays=%d (vs P=%d)" % (rp, xo, pr.get("relays", 0), P))
                best_xo = min(best_xo, xo)
            except compiler.CompileError:
                L.append("    right-sized P=%d: does not fit" % rp)
        summary.append((name, best_xo))
        L.append("")
    L.append("=== relay/capacity tradeoff: growing chain on a FIXED P=64 ring ===")
    L.append("(the constant read every step is relayed along the snake; relays are")
    L.append(" packets too, so they eat the ring -- at fixed P the kernel eventually")
    L.append(" overflows and must demote to a longer ring)")
    L.append("  %-7s %-7s %-7s %-9s %-12s" % ("chainN", "relays", "pkts", "fits P=64", "xover(flop)"))
    PFIX = 64
    for nlen in (4, 8, 16, 24, 28, 32):
        ck = _chain(nlen, PFIX)
        # compile without demotion to expose the capacity wall at this P
        try:
            cp = scheduler._compile_single(ck)
            a = en.activity(cp, "shift_opt")
            xo = en.crossover(a, "shift_opt", ck)
            L.append("  %-7d %-7d %-7d %-9s %-12.1f"
                     % (nlen, cp.get("relays", 0), len(cp["seed"]), "yes", xo))
        except compiler.CompileError:
            # demote to find where it does fit
            cp = compiler.compile_kernel(ck)
            L.append("  %-7d %-7d %-7d %-9s (demoted to P=%d)"
                     % (nlen, cp.get("relays", 0), len(cp["seed"]),
                        "NO", cp["config"]["rings"][0]["period"]))
    L.append("")
    L.append("=== multi-ring pipeline (energy summed across the hierarchy) ===")
    try:
        pk = kmod.load("sim/kernels/pipe2.json")
        pp = compiler.compile_kernel(pk)
        periods = [r["period"] for r in pp["config"]["rings"]]
        a = en.activity(pp, "shift_opt")
        m = en.madar_energy(a, "shift_opt")
        L.append("  pipe2 rings=%s transfers=%d  MADAR(flop)=%.0f pJ "
                 "(rot %.0f + cmp %.1f + xfer %.0f)"
                 % (periods, pp.get("transfers", 0), m["total"], m["rotation"],
                    m["compute"], m["relay"]))
        L.append("  -- the big outer ring's rotation dominates: a hierarchy pays for")
        L.append("     idle slow-ring rotation, so promote only what is needed soon.")
    except Exception as e:
        L.append("  pipe2 energy: %s" % str(e)[:60])
    L.append("")
    L.append("=== summary (best case across pricings) ===")
    for name, best in summary:
        rel = "WINS" if ec.E_PER_INSTR_CENTRAL > best else "loses"
        L.append("  %-9s best crossover %.1f pJ/instr -> %s vs central baseline %g pJ"
                 % (name, best, rel, ec.E_PER_INSTR_CENTRAL))
    L.append("")
    L.append("Honest reading: under the conservative reg-file shift proxy (6 pJ) on")
    L.append("these small oversized rings, rotation dominates and MADAR loses; under a")
    L.append("realistic flop-toggle shift (1 pJ) and/or right-sized rings the crossover")
    L.append("falls near or below a real in-order instruction (70 pJ), where MADAR wins.")
    L.append("Relays are a real, priced cost: a kernel dense in long-range dependences")
    L.append("pays for the copies that bridge them, and the tradeoff sweep shows the")
    L.append("crossover rising with chain length as relays and ring size grow.")
    return "\n".join(L)

def main():
    print(report())

if __name__ == "__main__":
    main()
