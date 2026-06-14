`timescale 1ns/1ps
// tb_loop.sv -- T3: a parked loop body executes once per revolution, with
// zero instruction fetches. One iteration IS one revolution of the ring.
// After R revolutions: acc = R*(R-1)/2, i = R, both instruction packets
// remain circulating (no steer kill yet -- that is T4).
module tb_loop
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

    // T3: a parked loop body -- one iteration per revolution, no fetch, no PC.
    // Seating (packets pass POS 0 in decreasing-initial-index order;
    // offset k names the packet (POS+k) mod P ahead of the instruction):
    //   slot 5: DATA acc = 0
    //   slot 4: DATA i   = 0
    //   slot 3: DATA one = 1
    //   slot 2: INSTR ADD src_a=3(acc) src_b=2(i)   dst=3 -> acc += i  (fires cy 14)
    //   slot 1: INSTR ADD src_a=3(i)   src_b=2(one) dst=3 -> i += 1    (fires cy 15)
    // Both ADDs read the OLD i each revolution (acc-update fires before i-update).
    // After R revolutions: acc = R*(R-1)/2, i = R.
    seed(5, data(64'd0));                       // acc
    seed(4, data(64'd0));                       // i
    seed(3, data(64'd1));                       // one
    seed(2, instr(OP_ADD, 3, 2, 3, 64'd0));     // acc += i
    seed(1, instr(OP_ADD, 3, 2, 3, 64'd0));     // i += 1

    spin(10 * P);                               // ten revolutions
    check("T3 acc = sum 0..9 = 45", slots[5].kind == K_DATA && slots[5].payload == 64'd45);
    check("T3 i = 10",              slots[4].kind == K_DATA && slots[4].payload == 64'd10);
    check("T3 one preserved",       slots[3].payload == 64'd1);
    check("T3 body still parked",   slots[2].kind == K_INSTR && slots[1].kind == K_INSTR);
    begin
      int strays = 0;
      for (int i = 0; i < P; i++)
        if (i != 1 && i != 2 && i != 3 && i != 4 && i != 5 && slots[i].kind != K_BUBBLE)
          strays++;
      check("T3 all other slots empty", strays == 0);
    end

    spin(5 * P);                                // five more revolutions
    check("T3 five more turns: acc = sum 0..14 = 105", slots[5].payload == 64'd105);
    check("T3 i = 15", slots[4].payload == 64'd15);

    $display("tb_loop: %0d tests, %0d errors", tests, errors);
    if (errors != 0) $fatal(1, "tb_loop FAILED");
    $finish;
  end
endmodule
