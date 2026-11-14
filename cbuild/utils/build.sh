#!/bin/sh
# Build the utilities: out/UTILMENU.SYS (linked at 0xC0100000) and out/<NAME>.BIN for each utility (at 0xC0110000).
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
mkdir -p out
[ -f ../out/r8rts.lib ] || ../build_rts.sh
cc() {  # name, link cmd, sources...
    n=$1; cmd=$2; shift 2
    "$CGT/bin/cl6x" -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far --mem_model:code=far \
        --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number --diag_warning=225 \
        -I"$CGT/include" -I.. -I../launcher --obj_directory=out/$n --asm_directory=out/$n -k \
        ../r8rt.c ../r8lib.c "$@" -z $cmd --library=../out/r8rts.lib -o out/$n.out -m out/$n.map
    "$CGT/bin/dis6x" out/$n.out > out/$n.dis
    if grep -qE "\.fphead|SPLOOP" out/$n.dis; then echo "error: compact instructions or SPLOOP in $n" >&2; exit 1; fi
}
mkdir -p out/utilmenu out/hello out/fileinfo out/listdiag out/varispd
cc utilmenu menu.cmd utilmenu.c && python3 -I ../../tools/mkutil.py out/utilmenu.out out/UTILMENU.SYS
mkdir -p out/utildiag; cc utildiag menu.cmd utildiag.c && python3 -I ../../tools/mkutil.py out/utildiag.out out/UTILDIAG.SYS
cc hello util.cmd hello.c && python3 -I ../../tools/mkutil.py out/hello.out out/HELLO.BIN
cc fileinfo util.cmd fileinfo.c browse.c rowval.asm && python3 -I ../../tools/mkutil.py out/fileinfo.out out/FILEINFO.BIN
cc listdiag util.cmd listdiag.c browse.c rowval.asm && python3 -I ../../tools/mkutil.py out/listdiag.out out/LISTDIAG.BIN
cc varispd util.cmd varispd.c browse.c rowval.asm && python3 -I ../../tools/mkutil.py out/varispd.out out/VARISPD.BIN
mkdir -p out/tonetest; cc tonetest util.cmd tonetest.c && python3 -I ../../tools/mkutil.py out/tonetest.out out/TONETEST.BIN
mkdir -p out/keylog; cc keylog util.cmd keylog.c && python3 -I ../../tools/mkutil.py out/keylog.out out/KEYLOG.BIN
mkdir -p out/battinfo; cc battinfo util.cmd battinfo.c browse.c rowval.asm && python3 -I ../../tools/mkutil.py out/battinfo.out out/BATTINFO.BIN
mkdir -p out/reamp; cc reamp util.cmd reamp.c browse.c rowval.asm wav.c outfile.c && python3 -I ../../tools/mkutil.py out/reamp.out out/REAMP.BIN
