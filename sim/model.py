"""MADAR ring-machine functional model. Semantics mirror the RTL exactly
(see docs/superpowers/specs/2026-06-12-madar-foundation-design.md)."""

MASK64 = (1 << 64) - 1
W_WIN  = 8
KIND   = {"bubble": 0, "data": 1, "instr": 2}
OPC    = {"ADD": 0, "SUB": 1, "CMPLT": 2, "STEER": 3, "XFER": 4, "MUL": 5}

class Packet:
    __slots__ = ("kind", "op", "src_a", "src_b", "dst", "payload")
    def __init__(self, kind, op="ADD", src_a=0, src_b=0, dst=0, payload=0):
        self.kind = kind; self.op = op
        self.src_a = src_a; self.src_b = src_b; self.dst = dst
        self.payload = payload & MASK64
    @staticmethod
    def data(payload):
        return Packet("data", payload=payload)
    @staticmethod
    def instr(op, src_a=0, src_b=0, dst=0, payload=0):
        return Packet("instr", op=op, src_a=src_a, src_b=src_b, dst=dst, payload=payload)
    def pack(self):
        return ((KIND[self.kind] << 79) | (OPC[self.op] << 76)
                | (self.src_a << 72) | (self.src_b << 68)
                | (self.dst << 64) | (self.payload & MASK64))
    def hex(self):
        return "%021x" % self.pack()

BUBBLE = Packet("bubble")

class Ring:
    """One circulating ring of P slots. shift_and_write applies one advance."""
    def __init__(self, rid, period):
        self.id = rid; self.P = period
        self.slots = [BUBBLE] * period
    def seed(self, slot, pkt):
        self.slots[slot] = pkt
    def shift_and_write(self, writes):
        """writes: list of (wr_idx, pkt) in port order; later wins. Each names a
        pre-shift index; the write lands at (wr_idx+1)%P, on the same edge as the
        shift -- replacing the named packet after it moves."""
        P = self.P
        nxt = [BUBBLE] * P
        for i in range(P):
            nxt[(i + 1) % P] = self.slots[i]
        for (wr_idx, pkt) in writes:
            nxt[(wr_idx + 1) % P] = pkt
        self.slots = nxt

def _alu(op, a, b):
    if op == "ADD":   return (a + b) & MASK64
    if op == "SUB":   return (a - b) & MASK64
    if op == "CMPLT": return 1 if (a & MASK64) < (b & MASK64) else 0
    if op == "MUL":   return (a * b) & MASK64
    return 0

class Machine:
    """Rings + stations. One step advances the named rings by one cycle:
    every station's outputs are computed from the CURRENT slots; then each
    advancing ring shifts and applies the writes that target it (later port
    wins). A write targeting ring R is applied only when R advances."""
    def __init__(self, cfg):
        self.rings = {r["id"]: Ring(r["id"], r["period"]) for r in cfg["rings"]}
        self.stations = list(cfg["stations"])
    def seed(self, rid, slot, pkt):
        self.rings[rid].seed(slot, pkt)
    def _station_writes(self):
        """Return {ring_id: [(wr_idx, pkt), ...]} in station (port) order."""
        out = {rid: [] for rid in self.rings}
        for st in self.stations:
            t = st["type"]
            if t in ("alu", "mul"):
                r = self.rings[st["ring"]]; P = r.P; pos = st["pos"]
                insn = r.slots[pos]
                fires = {"alu": ("ADD", "SUB", "CMPLT"), "mul": ("MUL",)}[t]
                if (insn.kind == "instr" and insn.op in fires
                        and 1 <= insn.dst <= W_WIN):
                    a = r.slots[(pos + insn.src_a) % P].payload
                    b = r.slots[(pos + insn.src_b) % P].payload
                    res = _alu(insn.op, a, b)
                    out[st["ring"]].append(((pos + insn.dst) % P, Packet.data(res)))
            elif t == "steer":
                r = self.rings[st["ring"]]; P = r.P; pos = st["pos"]
                kmax = st.get("kmax", W_WIN); insn = r.slots[pos]
                if insn.kind == "instr" and insn.op == "STEER":
                    pred = r.slots[(pos + insn.src_a) % P].payload
                    if pred != 0:
                        for k in range(min(insn.payload, kmax)):
                            out[st["ring"]].append(((pos + insn.dst + k) % P, BUBBLE))
            elif t == "xfer":
                r0 = self.rings[st["ring"]]; P0 = r0.P; pos0 = st["pos"]
                insn = r0.slots[pos0]
                if insn.kind == "instr" and insn.op == "XFER":
                    src = r0.slots[(pos0 + insn.src_a) % P0]
                    r1 = self.rings[st["to_ring"]]; pos1 = st["to_pos"]
                    out[st["to_ring"]].append(((pos1 + insn.dst) % r1.P, src))
            # 'io' is a no-op in the model (boundary only)
        return out
    def step(self, advancing):
        writes = self._station_writes()
        for rid in advancing:
            self.rings[rid].shift_and_write(writes[rid])
    def run(self, plan):
        for phase in plan:
            adv = list(phase["advance"])
            for _ in range(phase["cycles"]):
                self.step(adv)
    def dump(self):
        """Canonical final state: 'ring slot hex' per non-bubble slot, sorted."""
        lines = []
        for rid in sorted(self.rings):
            r = self.rings[rid]
            for s in range(r.P):
                if r.slots[s].kind != "bubble":
                    lines.append("%d %d %s" % (rid, s, r.slots[s].hex()))
        return "\n".join(lines)
