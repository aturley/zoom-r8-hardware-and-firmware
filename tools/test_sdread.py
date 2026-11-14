#!/usr/bin/env python3
"""SD-READN (sd_read_n: CMD18 + CMD12 with DMATRIG, in runs of up to 128 blocks) in sim674: multi-block reads return
exactly what single-block reads do for 2, 7, 8, 9 and 64 blocks. The card model drops byte 4094 of any multi-block
read of 4 KB or more sent without DMATRIG, as the R8's controller does, so a run without it fails here."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
C = r8sd.SdModel(sim, blocks=65536, hc=True)
sim.mocks[syms['delay_us']] = lambda s: None
BUF = 0xC0100000
R = {}; got = {}
def serial(m, text, until=' ok', timeout=400_000_000):
    n0 = len(m.in_data); m.out_q += text.encode()
    yield m.until(lambda: until.encode() in m.in_data[n0:], f'serial {text!r}', timeout)
    return m.in_data[n0:].decode('latin1')
RUNS = [(100, 2), (300, 7), (1000, 8), (2000, 9), (5000, 64)]
def script(m):
    r = {}; yield from r8usb.mac_enumeration(r)(m); R['enum'] = r
    yield m.until(lambda: b'running word' in m.in_data, 'banner', 100_000_000)
    R['init'] = yield from serial(m, 'SD-INIT .\r')
    for blk, n in RUNS:
        R[blk] = yield from serial(m, f'{blk} {n} {BUF} SD-READN .\r')
        got[blk] = sim.read(BUF, n * 512)
    R['after'] = yield from serial(m, f'777 {BUF} SD-READ .\r'); got['after'] = sim.read(BUF, 512)
    R['done'] = True
U.start(script)
class Stop(Exception): pass
def tr(s, pc, pk):
    U.tick()
    if R.get('done') or U.errors: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 12)
except Stop: pass
print(f'  {sim.cycle:,} cycles; host errors: {U.errors}; card errors: {C.errors}')
check('boots and the card initialises', R.get('enum', {}).get('done') and ' 0  ok' in R.get('init', ''))
for blk, n in RUNS:
    want = b''.join(C.block(blk + k) for k in range(n))
    check(f'{n} blocks from {blk}: status 0 and the data matches', ' 0  ok' in R.get(blk, '') and got.get(blk) == want)
check('a single-block read afterwards still works', ' 0  ok' in R.get('after', '') and got.get('after') == C.block(777))
check(f'no multi-block read faulted ({C.read_faults}): every run carried DMATRIG', C.read_faults == 0)
check('no card protocol errors', not C.errors)
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
