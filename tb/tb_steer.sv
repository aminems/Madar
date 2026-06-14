`timescale 1ns/1ps
// tb_steer.sv -- T4: steer kill -- predicate turns the circulating loop body
// to bubbles (loop exit). A STEER instruction with a true predicate kills
// `payload` packets starting `dst` slots ahead of itself; killing the loop
// body lets exactly the accumulator data packet survive.
module tb_steer
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
  logic                 wr_en  [9];
  logic [$clog2(P)-1:0] wr_idx [9];
  packet_t              wr_pkt [9];

  ring #(.P(P), .NW(9)) u_ring (
    .clk, .rst_n, .seed_en, .seed_idx, .seed_pkt, .advance,
    .slots_o(slots), .wr_en, .wr_idx, .wr_pkt
  );

  alu_station #(.P(P), .POS(0)) u_alu (
    .slots_i(slots), .wr_en(wr_en[0]), .wr_idx(wr_idx[0]), .wr_pkt(wr_pkt[0])
  );

  logic                 st_en  [8];
  logic [$clog2(P)-1:0] st_idx [8];
  packet_t              st_pkt [8];

  steer_station #(.P(P), .POS(8), .KMAX(8)) u_steer (
    .slots_i(slots), .wr_en(st_en), .wr_idx(st_idx), .wr_pkt(st_pkt)
  );

  for (genvar k = 0; k < 8; k++) begin : g_steer_ports
    assign wr_en[k+1]  = st_en[k];
    assign wr_idx[k+1] = st_idx[k];
    assign wr_pkt[k+1] = st_pkt[k];
  end

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

  // spin(n): exactly n posedges occur with advance high. All TB stimulus
  // changes at negedges; the ring samples at posedges — keep it that way.
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

    // T4: predicate kill of the circulating loop body = loop exit.
    // Two distinct things share the name "steer": the steer STATION is fixed
    // hardware at ring position POS=8; the steer INSTRUCTION is a circulating
    // packet (seeded at slot 0) that fires when it reaches that station.
    // Seating (P=16; ALU station at POS 0; steer station at POS 8):
    //   slot  8: DATA acc = 0        -- at offset 8 ahead of the steer
    //                                   INSTRUCTION, i.e. NOT in the kill run
    //                                   of offsets 0..7
    //   slot  7: DATA i   = 0
    //   slot  6: DATA one = 1
    //   slot  5: DATA limit = 9
    //   slot  4: DATA done = 0
    //   slot  3: INSTR ADD src_a=5(acc) src_b=4(i)   dst=5 -> acc += i  (fires cy 13 at ALU)
    //   slot  2: INSTR ADD src_a=5(i)   src_b=4(one) dst=5 -> i += 1    (fires cy 14 at ALU)
    //   slot  1: INSTR CMPLT src_a=4(limit) src_b=6(i) dst=3 -> done=(9<i) (fires cy 15 at ALU)
    //   slot  0: INSTR STEER src_a=4(done) dst=0 payload=8 -> kill offsets 0..7
    //            -- the steer INSTRUCTION, seeded at slot 0; it fires when it
    //               reaches the steer STATION at POS=8 (cycle 8 of each revolution)
    //
    // Revolution r leaves i=r, acc=r*(r-1)/2, done=(9<r).
    // done first true at r=10 (acc=45). The steer instruction fires at cycle 8
    // of each revolution reading the PREVIOUS revolution's done; in revolution
    // 11 it kills offsets 0..7 -- every packet except acc (offset 8).
    // Survivor: DATA 45.
    seed(8, data(64'd0));                              // acc
    seed(7, data(64'd0));                              // i
    seed(6, data(64'd1));                              // one
    seed(5, data(64'd9));                              // limit
    seed(4, data(64'd0));                              // done
    seed(3, instr(OP_ADD,   5, 4, 5, 64'd0));         // acc += i
    seed(2, instr(OP_ADD,   5, 4, 5, 64'd0));         // i += 1
    seed(1, instr(OP_CMPLT, 4, 6, 3, 64'd0));         // done = (9 < i)
    seed(0, instr(OP_STEER, 4, 0, 0, 64'd8));         // kill offsets 0..7 if done

    spin(9 * P);   // nine revolutions: loop still live
    check("T4 mid-loop acc = sum 0..8 = 36", slots[8].payload == 64'd36);
    check("T4 mid-loop body alive", slots[3].kind == K_INSTR && slots[0].kind == K_INSTR);

    spin(5 * P);   // through revolution 11's steer kill, plus slack
    begin
      int alive = 0;
      logic [63:0] survivor = '0;
      for (int s = 0; s < P; s++)
        if (slots[s].kind != K_BUBBLE) begin
          alive++;
          survivor = slots[s].payload;
        end
      check("T4 exactly one survivor", alive == 1);
      check("T4 survivor is acc = 45", survivor == 64'd45);
    end

    $display("tb_steer: %0d tests, %0d errors", tests, errors);
    if (errors != 0) $fatal(1, "tb_steer FAILED");
    $finish;
  end
endmodule
