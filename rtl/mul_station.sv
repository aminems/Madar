// mul_station.sv -- a fixed multiply station. Mirrors alu_station: fires when an
// OP_MUL instruction packet sweeps past, multiplying the two operands named by
// rotation offset and replacing the dst packet. The opcode check keeps it inert
// when a packet of another class passes -- do not loosen it.
/* verilator lint_off TIMESCALEMOD */
module mul_station
  import madar_pkg::*;
#(
  parameter int P   = 16,
  parameter int POS = 0
)(
  input  packet_t              slots_i [P],
  output logic                 wr_en,
  output logic [$clog2(P)-1:0] wr_idx,
  output packet_t              wr_pkt
);
/* verilator lint_on TIMESCALEMOD */
  /* verilator lint_off UNUSEDSIGNAL */
  packet_t     insn;
  /* verilator lint_on UNUSEDSIGNAL */
  logic [63:0] a, b;

  always_comb begin
    insn = slots_i[POS];
    a    = slots_i[(POS + int'(insn.src_a)) % P].payload;
    b    = slots_i[(POS + int'(insn.src_b)) % P].payload;
    wr_en  = (insn.kind == K_INSTR) && (insn.op == OP_MUL) &&
             (int'(insn.dst) inside {[1:W_WIN]});
    wr_idx = ($clog2(P))'((POS + int'(insn.dst)) % P);
    wr_pkt = '0;
    wr_pkt.kind    = K_DATA;
    wr_pkt.payload = a * b;
  end
endmodule
