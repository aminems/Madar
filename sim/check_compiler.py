"""Self-checking tests for the MADAR compiler (no pytest on the box)."""
import sys
from sim import kernel as kmod

_fails = 0
def expect(name, cond):
    global _fails
    if cond is not True:
        print("FAIL", name); _fails += 1
    else:
        print("pass", name)

MAC = {"ring": {"period": 16}, "stations": ["alu", "mul"],
       "inputs": [{"name": "a", "value": 6}, {"name": "b", "value": 7},
                  {"name": "c", "value": 3}], "consts": [],
       "ops": [{"id": "t0", "op": "MUL", "a": "a", "b": "b"},
               {"id": "t1", "op": "ADD", "a": "t0", "b": "c"}],
       "outputs": ["t1"]}

SUMLOOP = {"ring": {"period": 16}, "stations": ["alu"],
           "loop": {"trip": 10,
                    "state": [{"name": "acc", "init": 0}, {"name": "i", "init": 0}],
                    "consts": [{"name": "one", "value": 1}], "body": [],
                    "updates": [{"state": "acc", "op": "ADD", "a": "acc", "b": "i"},
                                {"state": "i", "op": "ADD", "a": "i", "b": "one"}],
                    "outputs": ["acc"]}}

def t_reference():
    expect("ref mac 6*7+3=45", kmod.reference(MAC)["t1"] == 45)
    expect("ref sumloop trip10=45", kmod.reference(SUMLOOP)["acc"] == 45)

POLY = {"ring": {"period": 32}, "stations": ["alu", "mul"],
        "inputs": [{"name": "a", "value": 2}, {"name": "x", "value": 3},
                   {"name": "b", "value": 4}, {"name": "c", "value": 5}], "consts": [],
        "ops": [{"id": "t0", "op": "MUL", "a": "a",  "b": "x"},
                {"id": "t1", "op": "ADD", "a": "t0", "b": "b"},
                {"id": "t2", "op": "MUL", "a": "t1", "b": "x"},
                {"id": "t3", "op": "ADD", "a": "t2", "b": "c"}],
        "outputs": ["t3"]}   # ((a*x)+b)*x+c = ((2*3)+4)*3+5 = 35

def _runprog(prog):
    from sim import program as pm
    m = pm.build(prog); m.run(prog["run"])
    return {o: m.rings[0].slots[sl].payload for o, sl in prog["outputs"].items()}

def t_compile_straightline():
    from sim import compiler
    prog = compiler.compile_kernel(MAC)
    expect("compile mac -> 45", _runprog(prog).get("t1") == 45)
    prog = compiler.compile_kernel(POLY)
    expect("compile poly -> 35", _runprog(prog).get("t3") == 35)

FIRTAP = {"ring": {"period": 16}, "stations": ["alu", "mul"],
          "loop": {"trip": 4,
                   "state": [{"name": "acc", "init": 0}],
                   "consts": [{"name": "c", "value": 3}, {"name": "x", "value": 2}],
                   "body": [{"id": "tmp", "op": "MUL", "a": "c", "b": "x"}],
                   "updates": [{"state": "acc", "op": "ADD", "a": "acc", "b": "tmp"}],
                   "outputs": ["acc"]}}   # acc += c*x each iter -> 4*6 = 24

def t_compile_loops():
    from sim import compiler
    prog = compiler.compile_kernel(SUMLOOP)
    expect("compile sumloop -> 45", _runprog(prog).get("acc") == 45)
    prog = compiler.compile_kernel(FIRTAP)
    expect("compile firtap -> 24", _runprog(prog).get("acc") == 24)

def t_compile_relays():
    """Kernels whose dependences exceed the W=8 window: the scheduler must insert
    COPY relays (same-ring XFER) and still reproduce the reference."""
    from sim import compiler, scheduler
    h = kmod.load("sim/kernels/horner.json")
    prog = scheduler.compile_kernel(h)
    expect("compile horner -> %d" % kmod.reference(h)["s4"],
           _runprog(prog).get("s4") == kmod.reference(h)["s4"])
    expect("horner needed relays", prog.get("relays", 0) > 0)
    cs = kmod.load("sim/kernels/chainsum.json")
    prog = scheduler.compile_kernel(cs)
    expect("compile chainsum -> %d" % kmod.reference(cs)["t11"],
           _runprog(prog).get("t11") == kmod.reference(cs)["t11"])
    expect("chainsum needed relays", prog.get("relays", 0) > 0)

def t_scheduler_vs_bruteforce():
    """On the small kernels both placers can seat, the constructive scheduler and
    the exhaustive backtracking placer must reach the same result."""
    from sim import compiler
    for nm, k, out, want in (("mac", MAC, "t1", 45), ("poly", POLY, "t3", 35)):
        a = _runprog(compiler.compile_kernel(k)).get(out)
        b = _runprog(compiler.compile_bruteforce(k)).get(out)
        expect("scheduler==bruteforce %s" % nm, a == want and b == want)

def t_compile_pipeline():
    """Multi-ring placement: a two-stage kernel placed across R0+R1 with a
    scheduled inter-ring XFER must reproduce the pipelined reference."""
    from sim import scheduler
    k = kmod.load("sim/kernels/pipe2.json")
    ref, _ = scheduler._pipeline_reference(k)            # {'out': 56}
    prog = scheduler.compile_kernel(k)
    m = _pm_build_run(prog)
    got = {o: m.rings[prog["out_ring"]].slots[sl].payload
           for o, sl in prog["outputs"].items()}
    expect("compile pipe2 -> %d" % ref["out"], got == ref)
    expect("pipe2 used two rings", len(prog["config"]["rings"]) == 2)
    expect("pipe2 has an inter-ring transfer", prog.get("transfers", 0) >= 1)

def t_demotion():
    """A kernel that overflows its declared ring is demoted to a longer ring in
    the period hierarchy rather than failing."""
    from sim import scheduler
    fan = {"ring": {"period": 16}, "stations": ["alu"],
           "inputs": [{"name": "x", "value": 5}],
           "consts": [{"name": "c%d" % i, "value": i + 1} for i in range(8)],
           "ops": [{"id": "o%d" % i, "op": "ADD", "a": "x", "b": "c%d" % i}
                   for i in range(8)],
           "outputs": ["o%d" % i for i in range(8)]}
    prog = scheduler.compile_kernel(fan)
    expect("fanout8 demoted off P=16",
           prog["config"]["rings"][0]["period"] > 16 and prog.get("demoted_from") == 16)

def _pm_build_run(prog):
    from sim import program as pm
    m = pm.build(prog); m.run(prog["run"]); return m

if __name__ == "__main__":
    t_reference()
    t_compile_straightline()
    t_compile_loops()
    t_compile_relays()
    t_scheduler_vs_bruteforce()
    t_compile_pipeline()
    t_demotion()
    print("check_compiler:", "OK" if _fails == 0 else ("%d FAILS" % _fails))
    sys.exit(1 if _fails else 0)
