#!/bin/sh
# Build the app launcher (flash-loaded, linked at 0xC0072000): out/launch.out/.dis/.map/.bin
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
mkdir -p out
[ -f ../out/r8rts.lib ] || ../build_rts.sh
"$CGT/bin/cl6x" -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number --diag_warning=225 \
    -I"$CGT/include" -I.. --obj_directory=out --asm_directory=out -k \
    ../r8rt.c ../r8lib.c launch.c jump.asm -z launch.cmd --library=../out/r8rts.lib -o out/launch.out -m out/launch.map
"$CGT/bin/dis6x" out/launch.out > out/launch.dis
if grep -qE "\.fphead|SPLOOP" out/launch.dis; then echo "error: compact instructions or SPLOOP" >&2; exit 1; fi
