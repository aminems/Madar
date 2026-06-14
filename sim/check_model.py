"""Self-checking tests for the MADAR Python model (no pytest on the box)."""
import sys
from sim.model import Packet, Ring, BUBBLE

def expect(name, cond):
    if not cond:
        print("FAIL", name); globals()["_fails"] = globals().get("_fails", 0) + 1
    else:
        print("pass", name)

def t_pack():
    p = Packet.data(42)
    expect("data pack kind=1", (p.pack() >> 79) == 1)
    expect("data pack payload", (p.pack() & ((1 << 64) - 1)) == 42)
    i = Packet.instr("ADD", 2, 1, 3, 0)
    expect("instr pack kind=2", (i.pack() >> 79) == 2)
    expect("instr op=ADD(0)", ((i.pack() >> 76) & 0x7) == 0)
    expect("bubble is empty", BUBBLE.kind == "bubble")

def t_rotate():
    r = Ring(0, 4)
    r.seed(1, Packet.data(7))
    r.shift_and_write([])           # one advance, no writes
    expect("rotated 1->2", r.slots[2].kind == "data" and r.slots[2].payload == 7)
    for _ in range(3):
        r.shift_and_write([])
    expect("home after P", r.slots[1].payload == 7)

def t_collision():
    from sim.model import Machine
    cfg = {"rings": [{"id": 0, "period": 16}],
           "stations": [{"type": "alu", "ring": 0, "pos": 0}]}
    m = Machine(cfg)
    m.seed(0, 3, Packet.data(7))
    m.seed(0, 2, Packet.data(35))
    m.seed(0, 1, Packet.instr("ADD", src_a=2, src_b=1, dst=2))
    m.run([{"cycles": 16, "advance": [0]}])
    s = m.rings[0].slots
    expect("T2 add 7+35=42", s[3].kind == "data" and s[3].payload == 42)
    expect("T2 operand kept", s[2].payload == 35)

def t_loop():
    from sim.model import Machine
    cfg = {"rings": [{"id": 0, "period": 16}],
           "stations": [{"type": "alu", "ring": 0, "pos": 0}]}
    m = Machine(cfg)
    m.seed(0, 5, Packet.data(0)); m.seed(0, 4, Packet.data(0))
    m.seed(0, 3, Packet.data(1))
    m.seed(0, 2, Packet.instr("ADD", 3, 2, 3))   # acc += i
    m.seed(0, 1, Packet.instr("ADD", 3, 2, 3))   # i   += 1
    m.run([{"cycles": 160, "advance": [0]}])
    expect("T3 acc=45 after 10", m.rings[0].slots[5].payload == 45)

def t_programs():
    from sim import program
    base = "sim/programs/"
    m = program.run(base + "t2_add.json")
    expect("prog t2 -> 42", m.rings[0].slots[3].payload == 42)
    m = program.run(base + "t3_loop.json")
    expect("prog t3 -> acc 45", m.rings[0].slots[5].payload == 45)
    m = program.run(base + "t4_steer.json")
    alive = [(rid, s) for rid in m.rings for s in range(m.rings[rid].P)
             if m.rings[rid].slots[s].kind != "bubble"]
    expect("prog t4 one survivor", len(alive) == 1)
    rid, s = alive[0]
    expect("prog t4 survivor 45", m.rings[rid].slots[s].payload == 45)
    m = program.run(base + "t5_xfer.json")
    expect("prog t5 landed R1[6]=99",
           m.rings[1].slots[6].kind == "data" and m.rings[1].slots[6].payload == 99)
    m = program.run(base + "mul_smoke.json")
    expect("prog mul 6*7=42", m.rings[0].slots[3].payload == 42)
    program.run(base + "hier3.json")  # smoke: must not raise
    expect("prog hier3 ran", True)

if __name__ == "__main__":
    globals()["_fails"] = 0
    t_pack(); t_rotate()
    t_collision(); t_loop()
    t_programs()
    n = globals()["_fails"]
    print("check_model:", "OK" if n == 0 else ("%d FAILS" % n))
    sys.exit(1 if n else 0)
