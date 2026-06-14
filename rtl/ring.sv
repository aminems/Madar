// ring.sv -- one circulating ring of packet slots: the ONLY storage in MADAR.
// Each cycle (advance=1) every packet moves one slot: slots[i] -> slots[(i+1)%P].
// Stations read the live slot array combinationally and overwrite packets via
// write ports. A write addressed to current index i is applied to (i+1)%P on
// the same edge as the shift -- i.e. it rewrites that packet wherever it lands
// -- so stations can rewrite packets that have already passed them. Later
// write ports win on conflict; the choreography (compiler) avoids conflicts.
/* verilator lint_off TIMESCALEMOD */
module ring
  import madar_pkg::*;
#(
  parameter int P  = 16,   // period: number of slots
  parameter int NW = 1     // number of station write ports
)(
  input  logic                 clk,
  input  logic                 rst_n,
  // Seed interface: the testbench loads the initial seating arrangement.
  input  logic                 seed_en,
  input  logic [$clog2(P)-1:0] seed_idx,
  input  packet_t              seed_pkt,
  input  logic                 advance,   // 0 = hold (while seeding/parked)
  // Station ports.
  output packet_t              slots_o [P],
  input  logic                 wr_en  [NW],
  input  logic [$clog2(P)-1:0] wr_idx [NW],  // current (pre-shift) index
  input  packet_t              wr_pkt [NW]
);
/* verilator lint_on TIMESCALEMOD */
  packet_t slots [P];
  assign slots_o = slots;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      /* verilator lint_off BLKLOOPINIT */
      for (int i = 0; i < P; i++) slots[i] <= '0;  // all bubbles
      /* verilator lint_on BLKLOOPINIT */
    end else if (seed_en) begin
      slots[seed_idx] <= seed_pkt;
    end else if (advance) begin
      /* verilator lint_off BLKLOOPINIT */
      for (int i = 0; i < P; i++) slots[(i + 1) % P] <= slots[i];
      /* verilator lint_on BLKLOOPINIT */
      for (int w = 0; w < NW; w++)
        if (wr_en[w]) slots[(int'(wr_idx[w]) + 1) % P] <= wr_pkt[w];
    end
  end
endmodule
