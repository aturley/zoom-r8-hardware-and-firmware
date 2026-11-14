#!/usr/bin/env python3
"""TONETEST.BIN (cbuild/utils/tonetest.c) in sim674 with the stock functions mocked (r8mock.py) and the stock audio
interrupt modelled: every PERIOD cycles (at a packet boundary with nothing in flight) the model flips the half flag
(0xC08137F4), fills that half of the input buffer (0x11F02828) with a counter, writes a marker into that half of the
output buffer (0x11F028A8, as the stock code's own output), then calls whatever the per-block pointer at 0x11F02E74
holds (the stock one is a mock that counts). Checks: the pointer is ours while it runs and put back after; the stock
pointer is still called every block; the output words carry 1 kHz (words 1, 3) and 440 Hz (words 0, 2) at -12 dBFS;
the input is recorded in order to A:\\TONETEST.WAV (24-bit stereo, 3 s); the popup reports the blocks and peak."""
import os, sys, struct, math
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock, mkutil
W = os.path.join(HERE, '..')
img, base, entry, top = mkutil.pack(os.path.join(W, 'cbuild', 'utils', 'out', 'tonetest.out'))
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

HOOK, RX, TX = 0x11F02E74, 0x11F02828, 0x11F028A8
B14 = 0xC0812B20                                    # the stock data page pointer
HALF = B14 + 4 * 821                                # the interrupt's half flag, B14[821] (0x1180E770)
STOCK = 0x11812000                                   # stands in for the stock per-block function
PERIOD = 700
sim = S.Sim(); sim.write(base, img[32:])
env = r8mock.R8Env(sim, {})
sim.wr32(HOOK, STOCK)
st = dict(stock=0, blocks=0, ctr=0, half=0, out=[], ours=0)
def stock(s): st['stock'] += 1
sim.mocks[STOCK] = stock

def block_event(s):
    if s.pend or s.branches: return s.cycle + 1   # only between packets with nothing in flight
    st['half'] ^= 1; h = st['half']
    s.wr32(HALF, h)
    for f in range(8):
        v = st['ctr']; st['ctr'] += 1
        s.write(RX + 8 * (8 * h + f), struct.pack('<ii', (v & 0x7FFFF) << 8, (-(v & 0x7FFFF)) << 8))
        s.write(TX + 16 * (8 * h + f), struct.pack('<4I', *[0xDEAD0000 + f] * 4))
    fn = s.rd32(HOOK)
    if fn != STOCK: st['ours'] += 1
    R = list(s.R); pend, br = s.pend, s.branches
    s.pend, s.branches = [], []
    s.R[S.ridx(1, 15)] = 0xC0E00000; s.R[S.ridx(1, 3)] = 0xCAFE7000
    s.next_event = None                           # interrupts are off inside the handler
    if fn == STOCK: stock(s)
    else: s.run(fn, 0xCAFE7000, 10 ** 12)
    s.R[:] = R; s.pend, s.branches = pend, br
    words = struct.unpack('<32i', s.read(TX + 16 * 8 * h, 128))
    if st['blocks'] < 3000: st['out'].append(words)
    st['blocks'] += 1
    return s.cycle + PERIOD
sim.next_event = PERIOD; sim.on_event = block_event

rc = sim.call(entry, (0, 0), max_cycles=10 ** 10)
pops = [x[1] for x in env.log if x[0] == 'popup']
print('  blocks %d (ours %d), stock calls %d, popups %s' % (st['blocks'], st['ours'], st['stock'], pops))
check('the per-block pointer is ours while it runs and put back after; the stock one still runs every block',
      sim.rd32(HOOK) == STOCK and st['ours'] >= 132300 // 8 and st['stock'] == st['blocks'])
w = [x for blk in st['out'][5:2005] for x in [blk[4 * f:4 * f + 4] for f in range(8)]]
def fit(y, f, rate=44100):
    wv = 2 * math.pi * f / rate
    c = [math.cos(wv * i) for i in range(len(y))]; s_ = [math.sin(wv * i) for i in range(len(y))]
    cc = sum(x * x for x in c); ss = sum(x * x for x in s_); cs = sum(a * b for a, b in zip(c, s_))
    yc = sum(a * b for a, b in zip(y, c)); ys = sum(a * b for a, b in zip(y, s_))
    det = cc * ss - cs * cs; a = (yc * ss - ys * cs) / det; b = (ys * cc - yc * cs) / det
    res = math.sqrt(sum((v - a * x - b * z) ** 2 for v, x, z in zip(y, c, s_)) / len(y))
    return math.hypot(a, b), res
FS = 2 ** 31
a1, r1 = fit([x[1] / FS for x in w], 1000); a3, _ = fit([x[3] / FS for x in w], 1000)
a0, r0 = fit([x[0] / FS for x in w], 440); a2, _ = fit([x[2] / FS for x in w], 440)
db = lambda x: 20 * math.log10(max(x, 1e-12))
print('  words 1/3: 1 kHz %.2f/%.2f dBFS (residual %.1f dB); words 0/2: 440 Hz %.2f/%.2f dBFS (residual %.1f dB)' % (db(a1), db(a3), db(r1 / a1), db(a0), db(a2), db(r0 / a0)))
check('output words 1 and 3 carry 1 kHz, 0 and 2 carry 440 Hz, at -12 dBFS, clean (residual below -50 dB); the stock samples are replaced',
      all(abs(db(a) + 12.04) < 0.1 for a in (a1, a3, a0, a2)) and db(r1 / a1) < -50 and db(r0 / a0) < -50
      and not any(x & 0xFFFF0000 == 0xDEAD0000 for x in sum((list(b) for b in st['out'][5:]), [])))
check('output samples are 24-bit, left-justified (low byte 0)', all(x & 0xFF == 0 for x in sum((list(b) for b in st['out'][5:50]), [])))
f = env.fs.get('A:\\TONETEST.WAV')
ok = f is not None and f[:4] == b'RIFF' and struct.unpack_from('<HHIIHH', f, 20) == (1, 2, 44100, 264600, 6, 24)
n = struct.unpack_from('<I', f, 40)[0] // 6 if ok else 0
fr = [(int.from_bytes(f[44 + 6 * i:47 + 6 * i], 'little', signed=True), int.from_bytes(f[47 + 6 * i:50 + 6 * i], 'little', signed=True)) for i in range(n)] if ok else []
first = fr[0][0] if fr else None
inorder = all(fr[i] == (((first + i) & 0x7FFFF), -((first + i) & 0x7FFFF)) for i in range(0, n, 997)) if fr else False
print('  wav: %s, %d frames, first input frame %s' % ('ok' if ok else 'missing/bad', n, fr[:2]))
check('the input is recorded in order, as 24-bit stereo, 3 s (132296 frames), to A:\\TONETEST.WAV', ok and n == 132296 and inorder)
check('popup: BLOCKS n PK peak (16-bit units)', len(pops) == 1 and pops[0].startswith('BLOCKS ') and ' PK ' in pops[0] and not env.fds)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
