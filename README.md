# MADAR — An Address-Free Processor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**MADAR** (Arabic *madār*, "orbit") is a novel CPU microarchitecture that deletes
the address. There is **no register file, no cache, and no program counter**. All
state lives in **rings of slots that circulate**, one position per clock;
instructions and data occupy the same slots and travel together; a value is named
not by an address but by its position in an orbit — a *(ring, phase)* coordinate;
and computation happens when a circulating instruction meets its operands at a
fixed station, on a schedule fixed when the program is laid out. A **hierarchy of
rings of geometrically increasing period replaces the cache hierarchy**, with
movement between rings scheduled in place of demand fetching.

This is the open-source artifact behind the paper *MADAR: An Address-Free
Processor*: a cycle-accurate SystemVerilog model, a Python functional model
cross-checked against it with Verilator, a constructive scheduling compiler, a
first-order energy model, and an AI-accelerator demonstrator.

> **Status:** research prototype, evaluated by architectural simulation (no
> synthesis or silicon). Everything here is reproducible — see *Build & run*.

## Why this matters for AI acceleration

The atom of dense AI compute is the multiply–accumulate, and a layer is
multiply–accumulates tiled and reduced. MADAR is a **programmable, cache-free
substrate for streaming inference**: the addressing machinery that dominates a
conventional core's energy is gone, and operand movement is choreographed instead
of fetched. In the demonstrator (`make ai`, cross-checked against the RTL):

- a real **dot product / matrix-vector / GEMM tile** compiles and runs on the model;
- a **streaming inner product** — both operands streamed through a *constant-size*
  compute ring — costs a **per-tap energy that does not grow with the reduction
  length** (~103 pJ/tap vs ~210 pJ for an in-order load-load-MAC), where a flat
  unroll's cost climbs;
- and the **operand reuse** that makes matrix multiplication efficient routes
  through the **ring-period hierarchy** (promote a shared operand to a fast ring;
  ~86 pJ/MAC, beating both a fused tile and a lone streaming dot).

The honest scope: MADAR is an efficiency win where the data movement is known
ahead of time and the ring is sized to the work — a new design point well suited
to streaming/edge AI inference, not a universal replacement.

## The four defining properties

MADAR is the conjunction of four properties; prior art reaches *pairs* of them but
not the full set:

1. **Circulating storage** — all state in rings addressed by *(ring, phase)*; no RF, no cache, no RAM.
2. **Co-circulation** — instructions and data circulate together in the same slots.
3. **Collision execution** — a fixed station computes when an instruction sweeps past, operands named by rotational offset, on a compile-time schedule (no dynamic matching).
4. **A period hierarchy for a memory hierarchy** — rings of geometrically increasing period stand in for cache levels, with transfer scheduled rather than triggered by a miss.

## Repository layout

| Path | What |
|---|---|
| `rtl/` | Synthesizable SystemVerilog: ring, ALU/MUL/steer/transfer stations |
| `tb/` | Self-checking SystemVerilog testbenches |
| `sim/` | Python functional model (semantics mirror the RTL), scheduling compiler, energy model, AI demonstrator |
| `sim/kernels/`, `sim/programs/` | Kernels and compiled programs (cross-checked) |
| `Makefile` | Build/run targets |

## Build & run

**Requirements:** [Verilator](https://www.veripool.org/verilator/) 5 for the RTL
tests and cross-check; Python 3 for the functional model, scheduler, energy model,
and AI demonstrator (no third-party packages).

```sh
make test           # build + run the SystemVerilog testbenches (Verilator)
make crosscheck     # assert the Python model and Verilator agree, program by program
make compile-check  # compile each kernel from dataflow; check the model and cross-check vs Verilator
make energy         # print the energy model: per-kernel crossover vs an in-order baseline
make ai             # the AI primitives (dot / matvec / GEMM, streaming MAC, tiling) + energy
make lint           # verilator --lint-only over the RTL
```

The model, scheduler, energy, and AI reports also run standalone (Python only):

```sh
python3 -m sim.check_compiler   # the constructive scheduler compiles + validates kernels
python3 -m sim.ai               # the AI-accelerator report
```

`scripts/remote-build.sh <target>` rsyncs this tree to a Linux box and runs
`make <target>` there (useful when Verilator is not installed locally).

## Citation

```bibtex
@misc{bergach2026madaraddressfreeprocessor,
      title={MADAR: An Address-Free Processor}, 
      author={Mohamed Amine Bergach},
      year={2026},
      eprint={2606.15535},
      archivePrefix={arXiv},
      primaryClass={cs.PF},
      url={https://arxiv.org/abs/2606.15535}, 
}
```

## License

MIT — see [`LICENSE`](LICENSE). Contributions and discussion are welcome.
