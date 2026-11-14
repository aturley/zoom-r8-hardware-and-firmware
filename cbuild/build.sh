#!/bin/sh
# Build flash-loaded C code for the R8:  ./build.sh NAME file1.c [file2.c ...]
# Produces out/NAME.out, out/NAME.map, out/NAME.bin (raw image for 0xC0070000) and out/NAME.dis.
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
NAME=$1; shift
mkdir -p out
[ -f out/r8rts.lib ] || ./build_rts.sh
"$CGT/bin/cl6x" -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number --diag_warning=225 \
    -I"$CGT/include" -I. --obj_directory=out --asm_directory=out -k \
    r8rt.c r8lib.c "$@" -z r8.cmd --library=out/r8rts.lib -o out/$NAME.out -m out/$NAME.map
"$CGT/bin/dis6x" out/$NAME.out > out/$NAME.dis
if grep -qE "\.fphead|SPLOOP" out/$NAME.dis; then echo "error: compact instructions or SPLOOP in out/$NAME.out" >&2; exit 1; fi
python3 -I ../tools/celf.py out/$NAME.out out/$NAME.bin
