// steer_station.sv -- control flow without a program counter. When an
// OP_STEER instruction sweeps past with a true predicate (operand src_a has
// nonzero payload), it kills `payload` packets starting `dst` slots ahead of
// itself (offset 0 = the steer itself), turning an already-circulating
// instruction group into bubbles so it never executes again.
// Branching = choosing which packets stay alive; loop exit = killing the body.
/* verilator lint_off TIMESCALEMOD */
module steer_station
  import madar_pkg::*;
#(
  parameter int P    = 16,
  parameter int POS  = 0,
  parameter int KMAX = W_WIN   // longest supported kill run — v0 caps kill
                               // runs at W_WIN=8 consecutive slots; this is
                               // a v0 cap, not an architectural bound (a
                               // steer could kill up to P).
)(
  input  packet_t              slots_i [P],
  output logic                 wr_en  [KMAX],
  output logic [$clog2(P)-1:0] wr_idx [KMAX],
  output packet_t              wr_pkt [KMAX]
);
/* verilator lint_on TIMESCALEMOD */
  // insn.src_b and insn.op are unused: steer reads only src_a (predicate) and
  // uses payload as the kill count. Waive the insn signal to keep -Wall clean.
  /* verilator lint_off UNUSEDSIGNAL */
  packet_t     insn;
  /* verilator lint_on UNUSEDSIGNAL */
  logic [63:0] pred;
  logic        fire;

  always_comb begin
    insn = slots_i[POS];
    pred = slots_i[(POS + int'(insn.src_a)) % P].payload;
    fire = (insn.kind == K_INSTR) && (insn.op == OP_STEER) && (pred != '0);
    for (int k = 0; k < KMAX; k++) begin
      wr_en[k]  = fire && (k < int'(insn.payload));
      wr_idx[k] = ($clog2(P))'((POS + int'(insn.dst) + k) % P);
      wr_pkt[k] = '0;  // a bubble
    end
  end
endmodule
