"""Load + validate a program.json and build a Machine from it."""
import json
from sim.model import Machine, Packet

def load(path):
    with open(path) as f:
        prog = json.load(f)
    for key in ("config", "seed", "run"):
        if key not in prog:
            raise ValueError("program missing '%s': %s" % (key, path))
    return prog

def build(prog):
    m = Machine(prog["config"])
    for e in prog["seed"]:
        pk = e["packet"]; kind = pk["kind"]
        if kind == "data":
            pkt = Packet.data(pk["payload"])
        elif kind == "instr":
            pkt = Packet.instr(pk["op"], pk.get("src_a", 0), pk.get("src_b", 0),
                               pk.get("dst", 0), pk.get("payload", 0))
        else:
            continue  # bubble: leave slot empty
        m.seed(e["ring"], e["slot"], pkt)
    return m

def run(path):
    prog = load(path); m = build(prog); m.run(prog["run"]); return m
