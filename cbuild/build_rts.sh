#!/bin/sh
# Rebuild the TI runtime helpers we need (integer/64-bit division, shifts, float<->long long, double/float
# division) from the compiler's own sources, with our flags: no compact instructions, no SPLOOP, interruptible
# loops. Output: out/r8rts.lib (linked instead of rts6740_elf.lib).
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
SRC="$CGT/lib/src"
mkdir -p out/rts; rm -f out/rts/*.obj out/r8rts.lib
FLAGS="-mv6740 --abi=eabi -O2 --mem_model:data=far --mem_model:const=far --no_compress --disable:sploop --interrupt_threshold=1000"
for f in divi.asm divu.asm remi.asm remu.asm divd.c divf.c imath64.c imath40.c llshift.c llabs.c mpyll.c \
         frcmpyd_div.c frcmpyf_div.c fixdlli.c fixdull.c fixflli.c fixfull.c fltllid.c fltllif.c fltulld.c fltullf.c; do
    "$CGT/bin/cl6x" $FLAGS -I"$CGT/include" -I"$SRC" --obj_directory=out/rts -c "$SRC/$f" >/dev/null 2>out/rts/$f.err \
        && rm -f out/rts/$f.err || { echo "skipped $f:"; head -3 out/rts/$f.err; }
done
"$CGT/bin/ar6x" r out/r8rts.lib out/rts/*.obj >/dev/null
echo "out/r8rts.lib: $(ls out/rts/*.obj | wc -l | tr -d ' ') objects"
