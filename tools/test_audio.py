#!/usr/bin/env python3
"""R8 Forth app audio I/O (cbuild/forth/aio.c) in sim674: AUDIO-INIT brings up McASP0 receive + transmit, PASS copies
the ADC input (serializer 1) to both transmit serializers with a Q8 gain, reports per-slot peaks, keeps up with
every slot (no overruns/underruns) and leaves the LCD pins alone."""
import os, sys, math
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb
W = os.path.join(HERE, '..')
K = 10                                                    # TSCL 10x fast: ~408 CPU cycles per audio slot
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=True, time_scale=K)
A0, A1 = 3_000_000, 1_000_000
def signal(k):                                            # slot k: even = left 1 kHz, odd = right 500 Hz
    t = (k // 2) / 44100
    v = int(A0 * math.sin(2 * math.pi * 1000 * t)) if k % 2 == 0 else int(A1 * math.sin(2 * math.pi * 500 * t))
    return v & 0xFFFFFF
B.rx_signal = signal
U = r8usb.Musb(sim, B)
sim.mocks[syms['delay_us']] = lambda s: None
res = {}
U.start(r8usb.mac_enumeration(res))
out = {}
def talk(text, wait_for, cycles=200_000_000):
    n0 = len(U.in_data); U.out_q += text.encode('latin1')
    deadline = sim.cycle + cycles
    while wait_for.encode() not in U.in_data[n0:] and sim.cycle < deadline: yield
    return U.in_data[n0:].decode('latin1')
def flow():
    deadline = sim.cycle + 2_000_000_000
    while not (res.get('done') or U.errors) and sim.cycle < deadline: yield
    while b'Ctrl-C stops a running word' not in U.in_data and sim.cycle < deadline: yield
    out['pass_noinit'] = yield from talk('10 256 PASS\r', ' ok')
    out['lcd_before'] = (B.gpio.get(0x38 + 4, 0) & (1 << 14), B.gpio.get(0xB0 + 4, 0) & 0x300)
    out['init'] = yield from talk('AUDIO-INIT .\r', ' ok')
    out['x0'] = len(B.samples); out['r0'] = len(B.rx_reads)
    out['pass'] = yield from talk('40 256 PASS\r', ' ok')
    out['x1'] = len(B.samples)
    out['half'] = yield from talk('20 128 PASS\r', ' ok')
    out['x2'] = len(B.samples)
    out['after'] = yield from talk('1 2 + .\r', ' ok')
    out['done'] = True
F = flow()
class Stop(Exception): pass
n_tr = [0]
def tr(s, pc, pk):
    U.tick()
    n_tr[0] += 1
    if n_tr[0] & 15 == 0:
        try: next(F)
        except StopIteration: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 13)
except Stop: pass
print(f'  run: {sim.cycle:,} cycles; host errors: {U.errors}')

def s24(v): v = (v >> 8) & 0xFFFFFF; return v - (1 << 24) if v & 0x800000 else v
def nums(t, key):
    w = t.split(); return int(w[w.index(key) + 1]) if key in w else None
print('  init:', repr(out.get('init', '')[-30:])); print('  pass:', repr(out.get('pass', '')[-110:]))
print('  half:', repr(out.get('half', '')[-110:]))
check('PASS before AUDIO-INIT refuses', 'AUDIO-INIT first' in out.get('pass_noinit', ''))
check('AUDIO-INIT: every clock step latched (returns true)', out.get('init', '').endswith('-1  ok\r\n'))
p = out.get('pass', '')
check('PASS reports peaks of both inputs (left 1 kHz %d, right 500 Hz %d)' % (A0, A1),
      nums(p, 'peak0') is not None and abs(nums(p, 'peak0') - A0) < A0 * 0.01 and abs(nums(p, 'peak1') - A1) < A1 * 0.01)
check('PASS: ~40 ms of frames, no errors', nums(p, 'frames') is not None and 1700 < nums(p, 'frames') < 1800 and nums(p, 'errors') == 0)
# output slot j (absolute index: the model pads missed slots) = a recent input slot of the same channel, times gain
ins = [signal(k) for k in range(B.rx_latest(sim) + 2)]
ins = [v - (1 << 24) if v & 0x800000 else v for v in ins]
def mismatches(j0, j1, gain):
    bad = n = seen = 0
    for j in range(j0, j1):
        c, v = B.samples[j]
        if c is None:                                     # idle time before the pass started: fine, else an underrun
            if seen >= 6: bad += 1
            continue
        if seen < 6: seen += 1; continue                  # first slots of a pass: zeros / the start-up race
        cands = [ins[i] * gain >> 8 for i in range(max(0, j - 8), min(len(ins), j + 2)) if i % 2 == j % 2]
        n += 1
        if s24(v) not in cands: bad += 1
    return n, bad
n1, bad1 = mismatches(out.get('x0', 0) + 8, out.get('x1', 0), 256)
print('  unity pass: output slots checked', n1, 'mismatches', bad1)
check('PASS: every output slot is a recent input slot of the same channel (unity gain)', n1 > 3000 and bad1 == 0)
n2, bad2 = mismatches(out.get('x1', 0) + 8, out.get('x2', 0), 128)
print('  half pass: output slots checked', n2, 'mismatches', bad2)
h = out.get('half', '')
check('second PASS (gain 128): channels still in order, output = input / 2, no errors',
      n2 > 1500 and bad2 == 0 and nums(h, 'peak0') is not None and abs(nums(h, 'peak0') - A0) < A0 * 0.01
      and abs(nums(h, 'peak1') - A1) < A1 * 0.01 and nums(h, 'errors') == 0)
check(f'transmitter never stalled after an underrun ({B.tx_restarts} restarts, {B.tx_stalls} stalls)', B.tx_stalls == 0 and B.tx_restarts >= 2)
check('both transmit serializers get the same data', B.xbuf4[-100:] == [v for c, v in B.samples[-100:]])
check('LCD pins untouched by the stock GPIO state', out.get('lcd_before') == (B.gpio.get(0x38 + 4, 0) & (1 << 14), B.gpio.get(0xB0 + 4, 0) & 0x300))
check('Forth still works afterwards', out.get('after', '').endswith('3  ok\r\n'))
check('no USB protocol errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
