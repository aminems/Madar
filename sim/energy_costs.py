"""Per-event energy constants for the MADAR energy model, in picojoules (pJ).

All 45 nm figures are from M. Horowitz, "Computing's Energy Problem (and what
we can do about it)," ISSCC 2014, Figure 1.1.9 ("Rough energy costs for various
operations in 45nm 0.9V"), read directly from the paper text.  The table gives
32-bit values; 64-bit datapath scaling is noted per constant.

These are first-order estimates; the report's conclusion is the crossover from
the sweep, not the absolute joules."""

# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

# Horowitz Fig 1.1.9: 32-bit integer ADD = 0.1 pJ.
# 64-bit ADD scales ~2x (double the adder width).
E_ALU = 0.2        # pJ  — 64-bit ADD/SUB/CMP

# Horowitz Fig 1.1.9: 32-bit integer MULT = 3.1 pJ.
# 64-bit multiplier width penalty is ~4x (Wallace-tree area scales quadratically).
E_MUL = 12.4       # pJ  — 64-bit MUL

# ---------------------------------------------------------------------------
# Register / word movement
# ---------------------------------------------------------------------------

# Horowitz Fig 1.1.9 instruction energy breakdown gives "Register File Access
# 6 pJ" for the register-file read/write cost of one instruction.  We use that
# 6 pJ DIRECTLY (no scaling) as a deliberately CONSERVATIVE proxy for one
# word transfer through MADAR's rotation network.  A true shift-register
# flop-to-flop transfer is much cheaper (~1 pJ: no address decode, no
# bitlines, no multi-porting), so 6 pJ OVERSTATES the rotation cost.  A
# tighter flop-toggle estimate would only favor MADAR, so we keep the
# upper bound.
E_WORD_SHIFT = 6.0  # pJ  — conservative reg-file-access proxy for a 64-bit
                    # word transfer (Horowitz reg-file access, used as-is)

E_WORD_SHIFT_OPT = 1.0  # pJ -- realistic shift-register flop-to-flop transfer of
                        # a 64-bit word (no address decode / bitlines / multiport);
                        # standard-cell flip-flop estimate at 45nm, the honest
                        # lower bound vs the conservative reg-file proxy above.

# ---------------------------------------------------------------------------
# SRAM
# ---------------------------------------------------------------------------

# Horowitz Fig 1.1.9: 8 KB SRAM 64-bit read = 10 pJ.  This is already a
# 64-bit access width, so no scaling required.
E_SRAM_ACCESS = 10.0  # pJ  — 64-bit single-port SRAM read or write (8 KB array)

# ---------------------------------------------------------------------------
# Per-instruction baseline (in-order core)
# ---------------------------------------------------------------------------

# Horowitz paper text (p. 11): "the programmable nature of a processor has
# high energy overhead, 70 pJ/instruction" — this is for a simple in-order
# processor at 45 nm.  The paper also breaks this down in Fig 1.1.9:
# I-Cache 25 pJ + Register File 6 pJ + Control ~39 pJ + ALU.
# 70 pJ is therefore the confirmed central value for an in-order core.
# Range: Cortex-A7 measured ~50-80 pJ/instr (FORTH technical report,
# Georgiou et al., 2014); ultra-low-power embedded cores reach ~5 pJ/instr
# (e.g., subthreshold designs); out-of-order cores exceed 100 pJ/instr.
E_PER_INSTR_CENTRAL = 70.0          # pJ — Horowitz ISSCC 2014, Fig 1.1.9 + text
E_PER_INSTR_RANGE   = (5.0, 200.0)  # pJ — sweep range across core classes

LOOP_OVERHEAD = 2  # baseline instrs/iteration (compare + branch)

# ---------------------------------------------------------------------------
# Source citations (one entry per constant key used in check_energy.py)
# ---------------------------------------------------------------------------

SOURCES = {
    "E_ALU": (
        "Horowitz, ISSCC 2014, Fig 1.1.9, 45nm 0.9V: 32-bit integer ADD = 0.1 pJ; "
        "x2 for 64-bit datapath width → 0.2 pJ"
    ),
    "E_MUL": (
        "Horowitz, ISSCC 2014, Fig 1.1.9, 45nm 0.9V: 32-bit integer MULT = 3.1 pJ; "
        "x4 for 64-bit (Wallace-tree area scales ~quadratically with width) → 12.4 pJ"
    ),
    "E_WORD_SHIFT": (
        "Horowitz, ISSCC 2014, Fig 1.1.9 instruction breakdown: Register File Access "
        "= 6 pJ, used directly (no scaling) as a conservative proxy for a 64-bit word "
        "transfer through the rotation network. This OVERSTATES a true shift-register "
        "flop-to-flop transfer (~1 pJ: no address decode / bitlines / multi-porting), "
        "so the rotation cost is deliberately upper-bounded; a tighter flop-toggle "
        "estimate would only favor MADAR"
    ),
    "E_WORD_SHIFT_OPT": "standard-cell flip-flop toggle estimate, ~1 pJ for a "
                        "64-bit word at 45nm; honest lower bound vs the reg-file proxy",
    "E_SRAM_ACCESS": (
        "Horowitz, ISSCC 2014, Fig 1.1.9, 45nm 0.9V: 8 KB SRAM 64-bit read = 10 pJ; "
        "already a 64-bit access — no additional scaling"
    ),
    "E_PER_INSTR": (
        "Horowitz, ISSCC 2014, p.11 text + Fig 1.1.9: simple in-order processor at "
        "45nm = 70 pJ/instruction (I-Cache 25 pJ + Reg-File 6 pJ + Control ~39 pJ); "
        "consistent with Cortex-A7 measured ~50-80 pJ/instr (Georgiou et al., FORTH "
        "TR-450, 2014)"
    ),
}
