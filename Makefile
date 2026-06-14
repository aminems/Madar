# MADAR -- build/sim via Verilator. Intended to run on the Linux build box
# (see ../CLAUDE.md). On macOS, drive it remotely: scripts/remote-build.sh.
VERILATOR ?= verilator

# M0 mechanics prototype: circulating ring + collision/steer/transfer stations.
# Per-test file lists (casa pattern) so early targets build before later
# stations exist.
RTL_RING  := rtl/madar_pkg.sv rtl/ring.sv rtl/alu_station.sv
RTL_STEER := rtl/madar_pkg.sv rtl/ring.sv rtl/alu_station.sv rtl/steer_station.sv
RTL_XFER  := rtl/madar_pkg.sv rtl/ring.sv rtl/xfer_station.sv

VFLAGS := -Wall --binary --timing -j 0

.PHONY: all test test-ring test-loop test-steer test-xfer lint clean sim-check crosscheck compile-check energy ai
.NOTPARALLEL:
all: test

test: test-ring test-loop test-steer test-xfer

# T1: rotation identity. T2: one ADD collision at the station.
test-ring:
	$(VERILATOR) $(VFLAGS) --Mdir obj_dir/ring --top-module tb_ring \
		$(RTL_RING) tb/tb_ring.sv -o Vtb_ring
	./obj_dir/ring/Vtb_ring

# T3: iteration is revolution -- a parked sum loop, one iteration per turn.
test-loop:
	$(VERILATOR) $(VFLAGS) --Mdir obj_dir/loop --top-module tb_loop \
		$(RTL_RING) tb/tb_loop.sv -o Vtb_loop
	./obj_dir/loop/Vtb_loop

# T4: steer kill -- predicate turns the circulating body to bubbles (loop exit).
test-steer:
	$(VERILATOR) $(VFLAGS) --Mdir obj_dir/steer --top-module tb_steer \
		$(RTL_STEER) tb/tb_steer.sv -o Vtb_steer
	./obj_dir/steer/Vtb_steer

# T5: scheduled migration -- a packet moves R0->R1 and keeps a 64-cycle orbit.
test-xfer:
	$(VERILATOR) $(VFLAGS) --Mdir obj_dir/xfer --top-module tb_xfer \
		$(RTL_XFER) tb/tb_xfer.sv -o Vtb_xfer
	./obj_dir/xfer/Vtb_xfer

# Lint each unit with all warnings on.
lint:
	$(VERILATOR) --lint-only -Wall --top-module ring rtl/madar_pkg.sv rtl/ring.sv
	$(VERILATOR) --lint-only -Wall --top-module alu_station rtl/madar_pkg.sv rtl/alu_station.sv
	$(VERILATOR) --lint-only -Wall --top-module steer_station rtl/madar_pkg.sv rtl/steer_station.sv
	$(VERILATOR) --lint-only -Wall --top-module xfer_station rtl/madar_pkg.sv rtl/xfer_station.sv
	$(VERILATOR) --lint-only -Wall --top-module mul_station rtl/madar_pkg.sv rtl/mul_station.sv

clean:
	rm -rf obj_dir *.vcd

# Python functional model self-check (runs from madar/, package import 'sim').
sim-check:
	python3 -m sim.check_model

# Cross-check the Python model against Verilator for every program (box only).
crosscheck: sim-check
	python3 -m sim.crosscheck

# Compile each kernel; check the model result and cross-check vs Verilator.
compile-check:
	python3 -m sim.compile_check

# Energy model: sanity-check the costs, then print the comparison + sweep.
energy:
	python3 -m sim.check_energy
	python3 -m sim.energy_report

# AI primitives: dot product / matrix-vector / GEMM tile compile + validate, then
# the AI energy reading (sized MAC loop wins; flat reduction loses to rotation).
ai:
	python3 -m sim.check_ai
	python3 -m sim.ai
