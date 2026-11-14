#!/usr/bin/env python3
"""Differential tests for sim674: each cbuild/simtests program is built with cl6x (run here in the simulator)
and with the Mac's clang (run natively). Hash and out[0..63] must match exactly.
Usage: python3 test_sim674.py [test names]   (needs ./build.sh to have been run for them)"""
import os, sys, subprocess, time, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S
CB = os.path.join(HERE, '..', 'cbuild')
SCR = os.environ.get('TMPDIR', '/tmp')
SEED, OUT = 12345, 0xc0200000

def native(t):
    exe = os.path.join(SCR, f'native_{t}')
    src = [os.path.join(CB, 'simtests', f'{t}.c'), os.path.join(CB, 'simtests', 'n_main.c')]
    subprocess.run(['clang', '-O2', '-ffp-contract=off', '-fno-builtin', '-Wno-everything', '-o', exe] + src, check=True)
    v = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split()
    return int(v[0], 16), [int(x, 16) for x in v[1:]]

def simulate(t):
    sim = S.Sim()
    base, img, syms, top = S.load_elf(sim, os.path.join(CB, 'out', f'{t}.out'))
    t0 = time.time()
    h = sim.call(syms['r8_entry'], (SEED, OUT))
    out = [sim.rd32(OUT + 4 * i) for i in range(64)]
    return h, out, sim, time.time() - t0

fails = []
TESTS = sys.argv[1:] or ['t_dbg', 't_dbl', 't_reverse', 't_wav', 't_arith', 't_resample']
import check_decode
for f in [os.path.join(CB, 'corpus', 'corpus.obj')] + [os.path.join(CB, 'out', f'{t}.out') for t in TESTS]:
    if check_decode.check(f, show=10): fails.append('decode ' + os.path.basename(f))
for t in TESTS:
    nh, nout = native(t)
    try:
        sh, sout, sim, dt = simulate(t)
    except Exception as e:
        print(f'FAIL {t}: {type(e).__name__}: {e}'); fails.append(t); continue
    ok = (sh, sout) == (nh, nout)
    print(f'{"PASS" if ok else "FAIL"} {t}: hash {sh:08x} (native {nh:08x}), {sim.ninstr:,} instructions, '
          f'{sim.cycle:,} cycles, {dt:.1f}s')
    if not ok:
        fails.append(t)
        for i, (a, b) in enumerate(zip(sout, nout)):
            if a != b: print(f'     out[{i}] sim {a:#x} native {b:#x}')
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
