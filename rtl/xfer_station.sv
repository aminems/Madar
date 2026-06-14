// xfer_station.sv -- scheduled migration between two rings: MADAR's whole
// memory hierarchy in miniature. When an OP_XFER instruction sweeps past on
// ring 0, the operand src_a slots ahead of it is copied into ring 1 at offset
// dst ahead of the transfer point. Promotion/demotion between fast small
// orbits and slow large ones is this, scheduled by the choreography -- there
// are no misses, only planned rendezvous.
/* verilator lint_off TIMESCALEMOD */
module xfer_station
  import madar_pkg::*;
#(
  parameter int P0   = 16,
  parameter int POS0 = 0,
  parameter int P1   = 64,
  parameter int POS1 = 0
)(
  input  packet_t               slots0_i [P0],
  output logic                  wr_en1,
  output logic [$clog2(P1)-1:0] wr_idx1,
  output packet_t               wr_pkt1
);
/* verilator lint_on TIMESCALEMOD */

  // insn.payload and insn.src_b are unused: XFER only uses src_a and dst.
  // Waive insn to keep -Wall clean.
  /* verilator lint_off UNUSEDSIGNAL */
  packet_t insn;
  /* verilator lint_on UNUSEDSIGNAL */

  always_comb begin
    insn    = slots0_i[POS0];
    wr_en1  = (insn.kind == K_INSTR) && (insn.op == OP_XFER);
    wr_idx1 = ($clog2(P1))'((POS1 + int'(insn.dst)) % P1);
    wr_pkt1 = slots0_i[(POS0 + int'(insn.src_a)) % P0];
  end
endmodule
