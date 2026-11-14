#!/bin/sh
# Build the Blippoo app: out/blippoo.out/.dis/.map and out/BLIPPOO.BIN (for R8APPS); also out/bp_host (the DSP on
# this computer, to render test files).
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
. ../font.sh
mkdir -p out
[ -f ../out/r8rts.lib ] || ../build_rts.sh
"$CGT/bin/cl6x" $FONTDEF -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number --diag_warning=225 \
    -I"$CGT/include" -I. -I../synth -I../forth --obj_directory=out --asm_directory=out -k \
    entry.asm vectors.asm ../synth/hw.c ../synth/lcd.c ../synth/panel.c ../synth/battery.c bp.c main.c ../forth/sd.c ../forth/fat.c ../r8lib.c \
    -z l2.cmd --library=../out/r8rts.lib -o out/blippoo.out -m out/blippoo.map
"$CGT/bin/dis6x" out/blippoo.out > out/blippoo.dis
if grep -qE "\.fphead|SPLOOP" out/blippoo.dis; then echo "error: compact instructions or SPLOOP" >&2; exit 1; fi
python3 -I ../../tools/mkapp.py out/blippoo.out out/BLIPPOO.BIN
cc -O2 -Wall -o out/bp_host bp_host.c bp.c -lm
