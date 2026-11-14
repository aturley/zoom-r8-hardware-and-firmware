#!/bin/sh
# Build the R8 app chooser as an app for RUN APP: out/chooser.out/.dis/.map and out/APP.BIN
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
. ../font.sh
mkdir -p out
[ -f ../out/r8rts.lib ] || ../build_rts.sh
"$CGT/bin/cl6x" $FONTDEF -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number --diag_warning=225 \
    -I"$CGT/include" -I. -I../synth -I../forth --obj_directory=out --asm_directory=out -k \
    entry.asm ../synth/hw.c ../synth/lcd.c ../synth/panel.c ../synth/battery.c ../forth/sd.c ../forth/fat.c ../forth/reload.c ../forth/tramp.asm main.c ../r8lib.c \
    -z l2.cmd --library=../out/r8rts.lib -o out/chooser.out -m out/chooser.map
"$CGT/bin/dis6x" out/chooser.out > out/chooser.dis
if grep -qE "\.fphead|SPLOOP" out/chooser.dis; then echo "error: compact instructions or SPLOOP" >&2; exit 1; fi
python3 -I ../../tools/mkapp.py out/chooser.out out/APP.BIN
