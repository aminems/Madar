`timescale 1ns/1ps
// tb_xfer.sv -- T5: scheduled migration -- a packet moves from R0 (period 16)
// to R1 (period 64) and keeps a 64-cycle orbit. The xfer_station fires when
// OP_XFER sweeps past POS0=0 on R0; the operand 1 slot ahead is copied into
// R1 at offset dst ahead of POS1. The packet then orbits R1 with period 64.
module tb_xfer
  import madar_pkg::*;
;
  localparam int P0 = 16;
  localparam int P1 = 64;

  logic clk = 1'b0, rst_n = 1'b0;
  /* verilator lint_off BLKSEQ */
  always #5 clk = ~clk;
  /* verilator lint_on BLKSEQ */

  // --- R0 (period 16, NW=1 — port driven by tied-off signals) ---------------
  logic                  seed_en0;
  logic [$clog2(P0)-1:0] seed_idx0;
  packet_t               seed_pkt0;
  logic                  advance0;
  packet_t               slots0 [P0];
  logic                  wr_en0  [1];
  logic [$clog2(P0)-1:0] wr_idx0 [1];
  packet_t               wr_pkt0 [1];

  ring #(.P(P0), .NW(1)) u_r0 (
    .clk, .rst_n,
    .seed_en(seed_en0), .seed_idx(seed_idx0), .seed_pkt(seed_pkt0),
    .advance(advance0), .slots_o(slots0),
    .wr_en(wr_en0), .wr_idx(wr_idx0), .wr_pkt(wr_pkt0)
  );

  // R0's write port is driven by the xfer_station (r1 side only); tie off R0's own port.
  assign wr_en0[0]  = 1'b0;
  assign wr_idx0[0] = '0;
  assign wr_pkt0[0] = '0;

  // --- R1 (period 64, NW=1 — port driven by xfer_station) ------------------
  logic                  seed_en1;
  logic [$clog2(P1)-1:0] seed_idx1;
  packet_t               seed_pkt1;
  logic                  advance1;
  packet_t               slots1 [P1];
  logic                  wr_en1  [1];
  logic [$clog2(P1)-1:0] wr_idx1 [1];
  packet_t               wr_pkt1 [1];

  ring #(.P(P1), .NW(1)) u_r1 (
    .clk, .rst_n,
    .seed_en(seed_en1), .seed_idx(seed_idx1), .seed_pkt(seed_pkt1),
    .advance(advance1), .slots_o(slots1),
    .wr_en(wr_en1), .wr_idx(wr_idx1), .wr_pkt(wr_pkt1)
  );

  // --- Transfer station: reads R0 slots, writes into R1 --------------------
  xfer_station #(.P0(P0), .POS0(0), .P1(P1), .POS1(0)) u_xfer (
    .slots0_i(slots0),
    .wr_en1(wr_en1[0]), .wr_idx1(wr_idx1[0]), .wr_pkt1(wr_pkt1[0])
  );

  // --- Bookkeeping ----------------------------------------------------------
  int errors = 0;
  int tests  = 0;

  function automatic packet_t data(input logic [63:0] v);
    packet_t p; p = '0; p.kind = K_DATA; p.payload = v; return p;
  endfunction

  // int args a/b/d/idx are wider than OFF_W (4-bit); upper bits are unused
  // by design -- the cast OFF_W'(...) drops them intentionally.
  /* verilator lint_off UNUSEDSIGNAL */
  function automatic packet_t instr(input op_e op, input int a, input int b,
                                    input int d, input logic [63:0] pay);
  /* verilator lint_on UNUSEDSIGNAL */
    packet_t p; p = '0; p.kind = K_INSTR; p.op = op;
    p.src_a = OFF_W'(a); p.src_b = OFF_W'(b); p.dst = OFF_W'(d);
    p.payload = pay; return p;
  endfunction

  /* verilator lint_off UNUSEDSIGNAL */
  task automatic seed0(input int idx, input packet_t p);
  /* verilator lint_on UNUSEDSIGNAL */
    @(negedge clk); seed_en0 = 1'b1; seed_idx0 = ($clog2(P0))'(idx); seed_pkt0 = p;
    @(negedge clk); seed_en0 = 1'b0;
  endtask

  /* verilator lint_off UNUSEDSIGNAL */
  task automatic seed1(input int idx, input packet_t p);
  /* verilator lint_on UNUSEDSIGNAL */
    @(negedge clk); seed_en1 = 1'b1; seed_idx1 = ($clog2(P1))'(idx); seed_pkt1 = p;
    @(negedge clk); seed_en1 = 1'b0;
  endtask

  // spin_both(n): n posedges with both rings advancing.
  // All TB stimulus changes at negedges; the ring samples at posedges -- keep it that way.
  task automatic spin_both(input int n);
    @(negedge clk); advance0 = 1'b1; advance1 = 1'b1;
    repeat (n) @(negedge clk);
    advance0 = 1'b0; advance1 = 1'b0;
  endtask

  // spin_r1_only(n): R0 parked, R1 orbits alone.
  task automatic spin_r1_only(input int n);
    @(negedge clk); advance1 = 1'b1;
    repeat (n) @(negedge clk);
    advance1 = 1'b0;
  endtask

  task automatic check(string name, logic cond);
    tests++;
    if (cond !== 1'b1) begin
      errors++; $display("FAIL [%0d] %s", tests, name);
    end else $display("pass [%0d] %s", tests, name);
  endtask

  initial begin
    seed_en0 = 1'b0; advance0 = 1'b0; seed_idx0 = '0; seed_pkt0 = '0;
    seed_en1 = 1'b0; advance1 = 1'b0; seed_idx1 = '0; seed_pkt1 = '0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;

    // Seat R0: the value and a transfer instruction one slot behind it.
    // slot 2: DATA 99         -- the operand (src_a=1 slot ahead of the instr)
    // slot 1: INSTR OP_XFER src_a=1, dst=5
    //         fires when it reaches transfer point POS0=0 (15 advances from slot 1)
    //         at cycle 15; writes to R1[(POS1+dst)%P1] = (0+5)%64 = 5,
    //         which lands at R1[6] via the ring's (wr_idx+1)%P write semantics.
    seed0(2, data(64'd99));
    seed0(1, instr(OP_XFER, 1, 0, 5, 64'd0));

    // 16 cycles on both rings: XFER fires at cycle 15, lands R1[6] at 16.
    spin_both(16);
    check("T5 packet arrived at (R1, idx 6)",
          slots1[6].kind == K_DATA && slots1[6].payload == 64'd99);
    check("T5 source copy still in R0",
          slots0[2].kind == K_DATA && slots0[2].payload == 64'd99);

    // The XFER must NOT be parked at the transfer point, or it would re-fire
    // into R1 every cycle while R0 is parked — the seating guarantees this.
    check("T5 XFER not parked at POS0",
          slots0[0].kind != K_INSTR || slots0[0].op != OP_XFER);

    // Park R0; let R1 orbit alone for one full period.
    spin_r1_only(64);
    check("T5 64-cycle orbit returns home",
          slots1[6].kind == K_DATA && slots1[6].payload == 64'd99);
    begin
      int alive = 0;
      for (int s = 0; s < P1; s++) if (slots1[s].kind != K_BUBBLE) alive++;
      check("T5 exactly one packet in R1", alive == 1);
    end

    $display("tb_xfer: %0d tests, %0d errors", tests, errors);
    if (errors != 0) $fatal(1, "tb_xfer FAILED");
    $finish;
  end
endmodule
