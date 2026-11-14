#!/usr/bin/env python3
"""Cycle cost of the loop voice (cbuild/looper/voice.c) on the C674x in sim674: 8 voices x 64 frames per block.
sim674 has no caches, so this is the instruction cost; DDR reads will add misses on hardware (DDR is cached)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S
sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(HERE, '..', 'cbuild', 'looper', 'out', 'bench.out'))
res = syms['bench_result']
class Stop(Exception): pass
def tr(s, pc, pk):
    if sim.rd32(res + 12): raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=50_000_000)
except Stop: pass
c = [sim.rd32(res + 4 * i) for i in range(4)]
per_frame = c[-1] / 64
print('cycles per 64-frame block, 8 voices:', c)
print(f'{per_frame:.0f} cycles per frame for 8 voices = {per_frame / 8:.0f} per voice; budget at 44.1 kHz / 360 MHz = 8163 per frame ({100 * per_frame / 8163:.1f}%)')
sys.exit(0 if 0 < per_frame < 4000 else 1)
