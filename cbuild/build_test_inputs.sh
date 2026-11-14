#!/bin/sh
# Build the programs that only the tests use (the apps have their own build.sh):
#   synth/out/synth.out          the standalone synth (test_synth.py, test_launcher.py)
#   corpus/corpus.obj            an instruction corpus the decoder is checked against (test_sim674.py)
#   out/t_*.out                  the simulator's differential test programs (test_sim674.py)
#   looper/out/bench.out         the loop-voice cycle benchmark (test_looper_cycles.py)
set -e
cd "$(dirname "$0")"
CGT=${CGT:-/Applications/ti/ti-cgt-c6000_8.5.0.LTS}
[ -f out/r8rts.lib ] || ./build_rts.sh
./synth/build.sh
"$CGT/bin/cl6x" -mv6740 --abi=eabi --no_compress -c corpus/corpus.asm --obj_directory=corpus >/dev/null
for t in t_dbg t_dbl t_reverse t_wav t_arith t_resample; do ./build.sh $t simtests/simmain.c simtests/$t.c >/dev/null; done
mkdir -p looper/out
"$CGT/bin/cl6x" -mv6740 --abi=eabi --c99 -O2 --mem_model:data=far --mem_model:const=far \
    --no_compress --disable:sploop --interrupt_threshold=1000 --display_error_number \
    -I"$CGT/include" -I. --obj_directory=looper/out --asm_directory=looper/out -k \
    forth/entry.asm looper/voice.c looper/bench.c -z forth/l2.cmd --library=out/r8rts.lib \
    -o looper/out/bench.out -m looper/out/bench.map >/dev/null
echo "built synth, corpus, simulator test programs and the looper benchmark"
