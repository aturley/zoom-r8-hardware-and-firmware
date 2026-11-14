#!/bin/sh
# Build the core R8 Forth app (core.c + main.c): out/forth.out/.dis/.map and out/APP.BIN (R8APPS/FORTH.BIN on the card)
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
. ../font.sh
mkdir -p out
[ -f ../out/r8rts.lib ] || ../build_rts.sh
"$CGT/bin/cl6x" $FONTDEF -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number --diag_warning=225 \
    -I"$CGT/include" -I. -I../synth --obj_directory=out --asm_directory=out -k \
    entry.asm ../synth/hw.c ../synth/lcd.c ../synth/panel.c ../synth/battery.c fat.c clock.c reload.c tramp.asm usb.c msc.c sd.c aio.c forth.c core.c main.c ../r8lib.c \
    -z l2.cmd --library=../out/r8rts.lib -o out/forth.out -m out/forth.map
"$CGT/bin/dis6x" out/forth.out > out/forth.dis
if grep -qE "\.fphead|SPLOOP" out/forth.dis; then echo "error: compact instructions or SPLOOP" >&2; exit 1; fi
python3 -I ../../tools/mkapp.py out/forth.out out/APP.BIN
