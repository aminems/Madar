"""Compile each kernel, assert the model runs the emitted program to the
kernel's reference result, and cross-check that program against Verilator."""
import json, os, sys
from sim import kernel as kmod, compiler, program as pm
from sim import crosscheck

KERNELS = ["mac", "poly", "sumloop", "firtap", "horner", "chainsum", "pipe2", "dot4"]

def model_ok(name):
    from sim import scheduler
    k = kmod.load("sim/kernels/%s.json" % name)
    ref = (scheduler._pipeline_reference(k)[0] if "pipeline" in k
           else kmod.reference(k))
    prog = compiler.compile_kernel(k)
    m = pm.build(prog); m.run(prog["run"])
    orr = prog.get("out_ring", 0)
    got = {o: m.rings[orr].slots[sl].payload for o, sl in prog["outputs"].items()}
    return all(got[o] == ref[o] for o in ref), got, ref

def rtl_ok(name):
    """Write the compiled program to obj_dir and cross-check it vs Verilator."""
    k = kmod.load("sim/kernels/%s.json" % name)
    prog = compiler.compile_kernel(k)
    os.makedirs("obj_dir", exist_ok=True)
    path = "obj_dir/k_%s.prog.json" % name
    with open(path, "w") as f:
        json.dump(prog, f)
    ok, p, r = crosscheck.check_one(path, "gen_k_%s" % name)
    return ok, p, r

def main():
    fails = 0
    for name in KERNELS:
        try:
            mok, got, ref = model_ok(name)
        except compiler.CompileError as e:
            print("FAIL compile", name, "--", e); fails += 1; continue
        print(("pass" if mok else "FAIL"), "compile-model", name,
              "" if mok else ("got %s want %s" % (got, ref)))
        if not mok:
            fails += 1; continue
        rok, p, r = rtl_ok(name)
        print(("pass" if rok else "FAIL"), "compile-rtl  ", name)
        if not rok:
            fails += 1; print("  PY :\n" + p); print("  RTL:\n" + r)
    print("compile-check:", "OK" if fails == 0 else ("%d FAILS" % fails))
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
