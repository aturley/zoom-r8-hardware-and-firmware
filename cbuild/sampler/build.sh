#!/bin/sh
# Build the R8 Sampler: the looper (looper.c, ../looper/voice.c) on the Forth core (../forth/core.c and its drivers).
# out/sampler.out/.dis/.map and out/SAMPLER.BIN (R8APPS/SAMPLER.BIN on the card).
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
. ../font.sh
F=../forth
mkdir -p out
[ -f ../out/r8rts.lib ] || ../build_rts.sh
"$CGT/bin/cl6x" $FONTDEF -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number --diag_warning=225 \
    -I"$CGT/include" -I. -I$F -I../synth -I../looper --obj_directory=out --asm_directory=out -k \
    $F/entry.asm vectors.asm ../synth/hw.c ../synth/lcd.c ../synth/panel.c ../synth/battery.c ../looper/voice.c \
    $F/fat.c $F/clock.c $F/reload.c $F/tramp.asm $F/usb.c $F/msc.c $F/sd.c $F/aio.c $F/forth.c $F/core.c looper.c main.c ../r8lib.c \
    -z $F/l2.cmd --library=../out/r8rts.lib -o out/sampler.out -m out/sampler.map
"$CGT/bin/dis6x" out/sampler.out > out/sampler.dis
if grep -qE "\.fphead|SPLOOP" out/sampler.dis; then echo "error: compact instructions or SPLOOP" >&2; exit 1; fi
python3 -I ../../tools/mkapp.py out/sampler.out out/SAMPLER.BIN
