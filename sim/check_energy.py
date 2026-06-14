"""Sanity + determinism checks for the MADAR energy model."""
import sys
from sim import energy_costs as ec

_fails = 0
def expect(name, cond):
    global _fails
    if cond is not True:
        print("FAIL", name); _fails += 1
    else:
        print("pass", name)

def t_costs():
    expect("E_ALU > 0", ec.E_ALU > 0)
    expect("E_MUL > E_ALU", ec.E_MUL > ec.E_ALU)
    expect("E_SRAM_ACCESS > 0", ec.E_SRAM_ACCESS > 0)
    expect("E_WORD_SHIFT > 0", ec.E_WORD_SHIFT > 0)
    lo, hi = ec.E_PER_INSTR_RANGE
    expect("baseline central in range", lo <= ec.E_PER_INSTR_CENTRAL <= hi)
    expect("every constant has a source",
           all(k in ec.SOURCES for k in
               ("E_ALU", "E_MUL", "E_WORD_SHIFT", "E_SRAM_ACCESS", "E_PER_INSTR")))

def t_energy():
    from sim import energy as en, kernel as kmod, compiler
    k = kmod.load("sim/kernels/sumloop.json")
    prog = compiler.compile_kernel(k)
    a = en.activity(prog, "shift")
    expect("sumloop R=10", a["R"] == 10)
    expect("sumloop alu_ops=20", a["alu_ops"] == 20)
    expect("sumloop mul_ops=0", a["mul_ops"] == 0)
    # clock-gated rotation: only live slots toggle, so strictly fewer shift-events
    # than a full-ring shift every cycle (160 cycles x 16 slots), and the exact
    # gated count is toggling(P, occupied) x advancing cycles.
    slots0 = [e["slot"] for e in prog["seed"] if e["ring"] == 0]
    expect("sumloop gated < ungated", 0 < a["shift_events"] < 160 * 16)
    expect("sumloop gated = toggling*adv",
           a["shift_events"] == en._toggling(16, slots0) * 160)
    asr = en.activity(prog, "sram")
    expect("sumloop sram = 2*adv (rotating pointer)", asr["shift_events"] == 2 * 160)
    expect("sumloop instr_count=43", en.instr_count(k) == 3 + 10 * 4)
    expect("mac instr_count=5",
           en.instr_count(kmod.load("sim/kernels/mac.json")) == 5)
    m = en.madar_energy(a, "shift")
    expect("madar total = rotation+compute+relay",
           abs(m["total"] - (m["rotation"] + m["compute"] + m["relay"])) < 1e-9)
    expect("crossover = total/instrs",
           abs(en.crossover(a, "shift", k) - m["total"] / en.instr_count(k)) < 1e-9)
    # a relay-bearing kernel must show nonzero XFER activity and relay energy
    hk = kmod.load("sim/kernels/horner.json")
    ha = en.activity(compiler.compile_kernel(hk), "shift_opt")
    expect("horner has xfer_ops>0", ha["xfer_ops"] > 0)
    expect("horner relay energy>0", en.madar_energy(ha, "shift_opt")["relay"] > 0)

def t_determinism():
    from sim import energy_report
    expect("report is deterministic", energy_report.report() == energy_report.report())

if __name__ == "__main__":
    t_costs()
    t_energy()
    t_determinism()
    print("check_energy:", "OK" if _fails == 0 else ("%d FAILS" % _fails))
    sys.exit(1 if _fails else 0)
