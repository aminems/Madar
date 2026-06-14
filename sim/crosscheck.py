"""Cross-check: for each program, assert the Python model's final-state dump
equals the Verilator run's dump. Intended to run on the Linux box (Verilator +
python3 both present). Each gen TB is built in its own obj_dir."""
import os, subprocess, sys
from sim import program, rtlgen

PROGS = ["t1_ring", "t2_add", "t3_loop", "t4_steer", "t5_xfer", "mul_smoke", "hier3",
         "relay", "move", "stream_mac", "stream_dot", "tile2"]
RTL = ["rtl/madar_pkg.sv", "rtl/ring.sv", "rtl/alu_station.sv", "rtl/mul_station.sv",
       "rtl/steer_station.sv", "rtl/xfer_station.sv"]

def py_dump_path(path):
    return program.run(path).dump()

def rtl_dump_path(path, top):
    prog = program.load(path)
    os.makedirs("obj_dir", exist_ok=True)
    sv = "obj_dir/%s.sv" % top
    with open(sv, "w") as f:
        f.write(rtlgen.emit(prog, top))
    mdir = "obj_dir/%s" % top
    build = (["verilator", "-Wall", "--binary", "--timing", "-j", "0",
              "--Mdir", mdir, "--top-module", top] + RTL + [sv, "-o", top])
    subprocess.run(build, check=True, stdout=subprocess.DEVNULL)
    out = subprocess.run(["%s/%s" % (mdir, top)], check=True,
                         stdout=subprocess.PIPE).stdout.decode()
    lines = [ln.strip() for ln in out.splitlines()
             if len(ln.split()) == 3 and ln.split()[0].isdigit()]
    return "\n".join(lines)

def check_one(path, top=None):
    """Return (ok, py_dump, rtl_dump) for an arbitrary program.json path."""
    if top is None:
        base = os.path.splitext(os.path.basename(path))[0]
        top = "gen_" + base.replace("-", "_").replace(".", "_")
    p = py_dump_path(path); r = rtl_dump_path(path, top)
    return (p == r, p, r)

def main():
    fails = 0
    for name in PROGS:
        ok, p, r = check_one("sim/programs/%s.json" % name, "gen_" + name)
        print(("pass" if ok else "FAIL"), "crosscheck", name)
        if not ok:
            fails += 1; print("  PY :\n" + p); print("  RTL:\n" + r)
    print("crosscheck:", "OK" if fails == 0 else ("%d FAILS" % fails))
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
