// alu_station.sv -- fixed compute hardware at one position on a ring.
// Collision execution: when an instruction packet of ALU class sweeps past
// (it is at slots[POS]), its operands -- named by relative offsets ahead of
// it -- are at slots[(POS+off)%P] right now, and the result is written over
// the packet dst slots ahead. Bubbles and data packets pass untouched.
/* verilator lint_off TIMESCALEMOD */
module alu_station
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
  // insn.payload is unused: ALU reads data from operand packets, not from the
  // instruction packet itself. Waive only the insn signal to keep -Wall clean.
  /* verilator lint_off UNUSEDSIGNAL */
  packet_t     insn;
  /* verilator lint_on UNUSEDSIGNAL */
  logic [63:0] a, b, result;

  always_comb begin
    insn = slots_i[POS];
    a    = slots_i[(POS + int'(insn.src_a)) % P].payload;
    b    = slots_i[(POS + int'(insn.src_b)) % P].payload;
    case (insn.op)
      OP_ADD:   result = a + b;
      OP_SUB:   result = a - b;
      OP_CMPLT: result = {63'd0, a < b};
      default:  result = '0;
    endcase
    // The opcode whitelist (not just the kind check) is what keeps this station inert when STEER/XFER packets pass — do not loosen it.
    wr_en = (insn.kind == K_INSTR) &&
            (insn.op inside {OP_ADD, OP_SUB, OP_CMPLT}) &&
            (int'(insn.dst) inside {[1:W_WIN]});
    wr_idx = ($clog2(P))'((POS + int'(insn.dst)) % P);
    wr_pkt = '0;
    wr_pkt.kind    = K_DATA;
    wr_pkt.payload = result;
  end
endmodule
