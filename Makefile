# FPGA bitstream build for the coil current servo.
# Derived from pavel-demin/red-pitaya-notes (tag 20251012, MIT); the kernel/
# Alpine/boot.bin targets were removed — we run the official Red Pitaya OS 3.x
# and only build the bitstream here. Full upstream flow is in git history.
#
# Requires Vivado 2025.1 on a supported Linux host (Ubuntu 24.04/22.04).
# Targets:
#   make cores                 package all cores/*.v as Vivado IP
#   make xpr                   create tmp/$(NAME).xpr Vivado project
#   make bit                   build tmp/$(NAME).bit
#   make $(NAME).bit.bin       byte-swapped bitstream for fpgautil on OS >= 2.0
#   make clean

NAME = coil_servo
PART = xc7z010clg400-1

FILES = $(wildcard cores/*.v)
CORES = $(FILES:.v=)

VIVADO = vivado -nolog -nojournal -mode batch
RM = rm -rf

.PRECIOUS: tmp/cores/% tmp/%.xpr tmp/%.bit

all: tmp/$(NAME).bit

cores: $(addprefix tmp/, $(CORES))

xpr: tmp/$(NAME).xpr

bit: tmp/$(NAME).bit

tmp/cores/%: cores/%.v
	mkdir -p $(@D)
	$(VIVADO) -source scripts/core.tcl -tclargs $* $(PART)

tmp/%.xpr: projects/% $(addprefix tmp/, $(CORES))
	mkdir -p $(@D)
	$(VIVADO) -source scripts/project.tcl -tclargs $* $(PART)

tmp/%.bit: tmp/%.xpr
	mkdir -p $(@D)
	$(VIVADO) -source scripts/bitstream.tcl -tclargs $*

%.bit.bin: tmp/%.bit
	python3 helpers/fpga-bit-to-bin.py -f $< $@

clean:
	$(RM) tmp *.bit.bin
	$(RM) .Xil usage_statistics_webtalk.html usage_statistics_webtalk.xml
	$(RM) vivado*.jou vivado*.log
