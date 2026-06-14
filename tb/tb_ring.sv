`timescale 1ns/1ps
// tb_ring.sv -- T1: a ring rotates its seating back to identity after exactly
// P cycles (storage IS the rotation). T2: an ADD instruction packet collides
// with two data packets at the ALU station; the result overwrites the dst
// packet. Without a steer kill an instruction re-executes every revolution
// (that is the model -- loops), so tests bound the cycle count exactly.
module tb_ring
  import madar_pkg::*;
;
  localparam int P = 16;

  logic clk = 1'b0, rst_n = 1'b0;
  /* verilator lint_off BLKSEQ */
  always #5 clk = ~clk;
  /* verilator lint_on BLKSEQ */

  logic                 seed_en;
  logic [$clog2(P)-1:0] seed_idx;
  packet_t              seed_pkt;
  logic                 advance;
  packet_t              slots [P];
  logic                 wr_en  [1];
  logic [$clog2(P)-1:0] wr_idx [1];
  packet_t              wr_pkt [1];

  ring #(.P(P), .NW(1)) u_ring (
    .clk, .rst_n, .seed_en, .seed_idx, .seed_pkt, .advance,
    .slots_o(slots), .wr_en, .wr_idx, .wr_pkt
  );

  alu_station #(.P(P), .POS(0)) u_alu (
    .slots_i(slots), .wr_en(wr_en[0]), .wr_idx(wr_idx[0]), .wr_pkt(wr_pkt[0])
  );

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
  task automatic seed(input int idx, input packet_t p);
  /* verilator lint_on UNUSEDSIGNAL */
    @(negedge clk); seed_en = 1'b1; seed_idx = ($clog2(P))'(idx); seed_pkt = p;
    @(negedge clk); seed_en = 1'b0;
  endtask

  task automatic spin(input int n);
    @(negedge clk); advance = 1'b1;
    repeat (n) @(negedge clk);
    advance = 1'b0;
  endtask

  task automatic check(string name, logic cond);
    tests++;
    if (cond !== 1'b1) begin
      errors++; $display("FAIL [%0d] %s", tests, name);
    end else $display("pass [%0d] %s", tests, name);
  endtask

  initial begin
    seed_en = 1'b0; advance = 1'b0; seed_idx = '0; seed_pkt = '0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;

    // T1: two data packets, one full revolution, identity restored.
    seed(3, data(64'd111));
    seed(9, data(64'd222));
    spin(P);
    check("T1 slot3 back home", slots[3].kind == K_DATA && slots[3].payload == 64'd111);
    check("T1 slot9 back home", slots[9].kind == K_DATA && slots[9].payload == 64'd222);
    check("T1 no stray packets", slots[0].kind == K_BUBBLE && slots[8].kind == K_BUBBLE);
    begin
      int strays = 0;
      for (int i = 0; i < P; i++)
        if (i != 3 && i != 9 && slots[i].kind != K_BUBBLE) strays++;
      check("T1 all other slots empty", strays == 0);
    end

    // T2: reset, then seat a = 7 (slot 3), b = 35 (slot 2), and
    // ADD src_a=2 src_b=1 dst=2 (slot 1): operands are 2 and 1 slots ahead,
    // result lands over `a`. The instruction fires when it reaches POS 0
    // (cycle 15); after exactly P cycles the result is home at slot 3.
    rst_n = 1'b0; repeat (2) @(negedge clk); rst_n = 1'b1;
    seed(3, data(64'd7));
    seed(2, data(64'd35));
    seed(1, instr(OP_ADD, 2, 1, 2, 64'd0));
    spin(P);
    check("T2 result replaced dst", slots[3].kind == K_DATA && slots[3].payload == 64'd42);
    check("T2 operand b untouched", slots[2].kind == K_DATA && slots[2].payload == 64'd35);
    check("T2 instruction still circulating", slots[1].kind == K_INSTR);
    begin
      int strays = 0;
      for (int i = 0; i < P; i++)
        if (i != 1 && i != 2 && i != 3 && slots[i].kind != K_BUBBLE) strays++;
      check("T2 all other slots empty", strays == 0);
    end

    $display("tb_ring: %0d tests, %0d errors", tests, errors);
    if (errors != 0) $fatal(1, "tb_ring FAILED");
    $finish;
  end
endmodule
