"""Kernel IR: load + the reference interpreter (the intended result a seating
must reproduce). Straight-line kernels have 'ops'; counted loops have 'loop'."""
import json
from sim.model import _alu

def load(path):
    with open(path) as f:
        k = json.load(f)
    if "loop" not in k and "ops" not in k and "pipeline" not in k:
        raise ValueError("kernel needs 'ops', 'loop', or 'pipeline': %s" % path)
    return k

def reference(kernel):
    """Return {output_name: value} for the kernel's intended semantics."""
    if "loop" in kernel:
        lp = kernel["loop"]
        state = {s["name"]: s["init"] for s in lp["state"]}
        consts = {c["name"]: c["value"] for c in lp.get("consts", [])}
        for _ in range(lp["trip"]):
            env = dict(state); env.update(consts)
            for op in lp.get("body", []):
                env[op["id"]] = _alu(op["op"], env[op["a"]], env[op["b"]])
            new = {}
            for u in lp["updates"]:
                new[u["state"]] = _alu(u["op"], env[u["a"]], env[u["b"]])
            state.update(new)
        return {o: state[o] for o in lp["outputs"]}
    env = {c["name"]: c["value"] for c in kernel.get("inputs", [])}
    env.update({c["name"]: c["value"] for c in kernel.get("consts", [])})
    for op in kernel["ops"]:
        env[op["id"]] = _alu(op["op"], env[op["a"]], env[op["b"]])
    return {o: env[o] for o in kernel["outputs"]}
