#!/bin/sh
# Build the standalone chord synth: out/synth.out/.bin/.dis/.map, then the R8SYS image via tools/standalone_c.py
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
mkdir -p out
[ -f ../out/r8rts.lib ] || ../build_rts.sh
"$CGT/bin/cl6x" -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number \
    -I"$CGT/include" -I. --obj_directory=out --asm_directory=out -k \
    entry.asm hw.c lcd.c panel.c battery.c audio.c synth.c main.c ../r8lib.c \
    -z l2.cmd --library=../out/r8rts.lib -o out/synth.out -m out/synth.map
"$CGT/bin/dis6x" out/synth.out > out/synth.dis
if grep -qE "\.fphead|SPLOOP" out/synth.dis; then echo "error: compact instructions or SPLOOP" >&2; exit 1; fi
python3 -I ../../tools/mkapp.py out/synth.out out/APP.BIN       # for RUN APP (app launcher firmware)
