"""Generate a self-contained Verilator testbench from a program.json.
The dump lines printed by the testbench match Machine.dump() exactly."""
OPC = {"ADD": "OP_ADD", "SUB": "OP_SUB", "CMPLT": "OP_CMPLT",
       "STEER": "OP_STEER", "XFER": "OP_XFER", "MUL": "OP_MUL"}

def _nw(cfg, rid):
    """Total write ports feeding ring rid (alu/mul/xfer = 1; steer = kmax)."""
    n = 0
    for st in cfg["stations"]:
        if st.get("ring") == rid and st["type"] in ("alu", "mul"):
            n += 1
        if st.get("to_ring") == rid and st["type"] == "xfer":
            n += 1
        if st.get("ring") == rid and st["type"] == "steer":
            n += st.get("kmax", 8)
    return n

def emit(prog, name):
    cfg = prog["config"]
    rings = cfg["rings"]
    L = []
    L.append("`timescale 1ns/1ps")
    L.append("module %s import madar_pkg::*; ;" % name)
    L.append("  logic clk=0, rst_n=0;")
    L.append("  /* verilator lint_off BLKSEQ */")
    L.append("  always #5 clk=~clk;")
    L.append("  /* verilator lint_on BLKSEQ */")
    # per-ring signals + ring instances
    for r in rings:
        rid, P = r["id"], r["period"]; nw = max(_nw(cfg, rid), 1)
        cw = "$clog2(%d)" % P
        L.append("  logic seed_en%d; logic [%s-1:0] seed_idx%d; packet_t seed_pkt%d;"
                 % (rid, cw, rid, rid))
        L.append("  logic adv%d;" % rid)
        L.append("  packet_t slots%d [%d];" % (rid, P))
        L.append("  logic we%d [%d]; logic [%s-1:0] wi%d [%d]; packet_t wp%d [%d];"
                 % (rid, nw, cw, rid, nw, rid, nw))
        L.append("  ring #(.P(%d), .NW(%d)) u_r%d (.clk, .rst_n, "
                 ".seed_en(seed_en%d), .seed_idx(seed_idx%d), .seed_pkt(seed_pkt%d), "
                 ".advance(adv%d), .slots_o(slots%d), .wr_en(we%d), .wr_idx(wi%d), "
                 ".wr_pkt(wp%d));" % (P, nw, rid, rid, rid, rid, rid, rid, rid, rid, rid))
    # stations: assign each its write port(s) on the target ring
    port = {r["id"]: 0 for r in rings}
    for si, st in enumerate(cfg["stations"]):
        t = st["type"]
        if t in ("alu", "mul"):
            rid = st["ring"]; p = port[rid]; port[rid] += 1
            mod = "alu_station" if t == "alu" else "mul_station"
            L.append("  %s #(.P(%d), .POS(%d)) u_s%d (.slots_i(slots%d), "
                     ".wr_en(we%d[%d]), .wr_idx(wi%d[%d]), .wr_pkt(wp%d[%d]));"
                     % (mod, _period(rings, rid), st["pos"], si, rid,
                        rid, p, rid, p, rid, p))
        elif t == "steer":
            rid = st["ring"]; kmax = st.get("kmax", 8); base = port[rid]; port[rid] += kmax
            L.append("  logic se%d [%d]; logic [$clog2(%d)-1:0] sidx%d [%d]; packet_t spk%d [%d];"
                     % (si, kmax, _period(rings, rid), si, kmax, si, kmax))
            L.append("  steer_station #(.P(%d), .POS(%d), .KMAX(%d)) u_s%d "
                     "(.slots_i(slots%d), .wr_en(se%d), .wr_idx(sidx%d), .wr_pkt(spk%d));"
                     % (_period(rings, rid), st["pos"], kmax, si, rid, si, si, si))
            for k in range(kmax):
                L.append("  assign we%d[%d]=se%d[%d]; assign wi%d[%d]=sidx%d[%d]; "
                         "assign wp%d[%d]=spk%d[%d];"
                         % (rid, base + k, si, k, rid, base + k, si, k,
                            rid, base + k, si, k))
        elif t == "xfer":
            tr = st["to_ring"]; p = port[tr]; port[tr] += 1
            L.append("  xfer_station #(.P0(%d), .POS0(%d), .P1(%d), .POS1(%d)) u_s%d "
                     "(.slots0_i(slots%d), .wr_en1(we%d[%d]), .wr_idx1(wi%d[%d]), "
                     ".wr_pkt1(wp%d[%d]));"
                     % (_period(rings, st["ring"]), st["pos"], _period(rings, tr),
                        st["to_pos"], si, st["ring"], tr, p, tr, p, tr, p))
    # tie off unused write ports (rings whose nw rounded up to 1 but have 0 stations)
    for r in rings:
        rid = r["id"]
        if port[rid] == 0:
            L.append("  assign we%d[0]=1'b0; assign wi%d[0]='0; assign wp%d[0]='0;"
                     % (rid, rid, rid))
    # driver: reset, seed, run plan, dump
    L.append("  initial begin")
    for r in rings:
        rid = r["id"]
        L.append("    seed_en%d=0; adv%d=0; seed_idx%d='0; seed_pkt%d='0;"
                 % (rid, rid, rid, rid))
    L.append("    repeat (2) @(negedge clk); rst_n=1;")
    # seed each listed slot (one per negedge, per ring)
    for e in prog["seed"]:
        rid = e["ring"]; pk = e["packet"]
        L.append("    @(negedge clk); seed_en%d=1; seed_idx%d=%d; %s"
                 % (rid, rid, e["slot"], _mkpkt("seed_pkt%d" % rid, pk)))
        L.append("    @(negedge clk); seed_en%d=0;" % rid)
    # run plan
    for ph in prog["run"]:
        adv = set(ph["advance"])
        L.append("    @(negedge clk); %s"
                 % " ".join("adv%d=%d;" % (r["id"], 1 if r["id"] in adv else 0)
                            for r in rings))
        L.append("    repeat (%d) @(negedge clk);" % ph["cycles"])
        L.append("    %s" % " ".join("adv%d=0;" % r["id"] for r in rings))
    # dump (matches Machine.dump(): non-bubble, ring/slot order)
    L.append("    #1;")
    for r in rings:
        rid, P = r["id"], r["period"]
        L.append("    for (int s=0; s<%d; s++) if (slots%d[s].kind != K_BUBBLE) "
                 "$display(\"%%0d %%0d %%021x\", %d, s, slots%d[s]);" % (P, rid, rid, rid))
    L.append("    $finish; end")
    L.append("endmodule")
    return "\n".join(L) + "\n"

def _period(rings, rid):
    for r in rings:
        if r["id"] == rid:
            return r["period"]
    raise KeyError(rid)

def _mkpkt(var, pk):
    if pk["kind"] == "data":
        return "%s='0; %s.kind=K_DATA; %s.payload=64'd%d;" % (var, var, var, pk["payload"])
    if pk["kind"] == "instr":
        return ("%s='0; %s.kind=K_INSTR; %s.op=%s; %s.src_a=%d; %s.src_b=%d; "
                "%s.dst=%d; %s.payload=64'd%d;"
                % (var, var, var, OPC[pk["op"]], var, pk.get("src_a", 0),
                   var, pk.get("src_b", 0), var, pk.get("dst", 0),
                   var, pk.get("payload", 0)))
    return "%s='0;" % var
