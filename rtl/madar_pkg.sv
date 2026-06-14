// madar_pkg.sv -- shared types for the MADAR ring-machine prototype (M0).
// One slot = one packet; a value's address is (ring, phase). Operand and
// result references are relative rotation offsets, in slots AHEAD of the
// instruction packet (a packet k ahead passed any station k cycles earlier).
/* verilator lint_off TIMESCALEMOD */
package madar_pkg;
/* verilator lint_on TIMESCALEMOD */

  parameter int W_WIN = 8;                  // station window: offsets 1..W_WIN
  parameter int OFF_W = $clog2(W_WIN + 1);  // 4 bits: holds 0..8

  typedef enum logic [1:0] {
    K_BUBBLE = 2'd0,   // empty slot
    K_DATA   = 2'd1,   // operand in rotation
    K_INSTR  = 2'd2    // instruction in rotation
  } kind_e;

  typedef enum logic [2:0] {
    OP_ADD   = 3'd0,
    OP_SUB   = 3'd1,
    OP_CMPLT = 3'd2,   // dst.payload = (a < b) ? 1 : 0  (unsigned)
    OP_STEER = 3'd3,   // if a != 0: kill `payload` slots starting dst ahead
    OP_XFER  = 3'd4,   // copy operand a into the partner ring at offset dst
    OP_MUL   = 3'd5    // dst.payload = a * b (low 64 bits)
  } op_e;

  typedef struct packed {
    kind_e            kind;
    op_e              op;       // K_INSTR only
    logic [OFF_W-1:0] src_a;    // operand offset ahead, 1..W_WIN
    logic [OFF_W-1:0] src_b;
    logic [OFF_W-1:0] dst;      // result offset ahead (OP_STEER: kill start)
    logic [63:0]      payload;  // K_DATA: value; OP_STEER: kill count
  } packet_t;

endpackage
