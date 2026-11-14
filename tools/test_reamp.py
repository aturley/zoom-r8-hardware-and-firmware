#!/usr/bin/env python3
"""REAMP.BIN (cbuild/utils/reamp.c) in sim674 with the stock functions mocked (r8mock.py), a scripted menu engine
(ENTER on rows) and the stock audio interrupt modelled as in test_tonetest.py: every PERIOD cycles, between packets,
the model flips the half flag (B14[821]), fills that half of the input buffer with a counter (left n, right -n),
calls the per-block pointer at 0x11F02E74, and keeps the output words. The UI event reader returns STOP once enough
blocks have passed after the end of the track. Checks: the line out carries the source in order then silence; the
headphones carry the input of the same block; the recording is the input from the first block, without the first
47 frames, going on past the end of the track until STOP; 24-bit stereo with the real length in the header; the
pointer is put back; no xruns; a source at another sample rate is refused."""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock, mkutil
W = os.path.join(HERE, '..')
img, base, entry, top = mkutil.pack(os.path.join(W, 'cbuild', 'utils', 'out', 'reamp.out'))
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

HOOK, RX, TX = 0x11F02E74, 0x11F02828, 0x11F028A8
HALF = 0xC0812B20 + 4 * 821
STOCK = 0x11812000
PERIOD = 1500
STR = {0xc06e77dd: 'Please Wait...', 0xc06e7960: 'ACCESS ERROR', 0xc06e785d: 'Invalid File'}
NSRC = 3000
src = b''.join(struct.pack('<hh', 1000 + i, -1000 - i) for i in range(NSRC))
def wav16(data, rate=44100, ch=2):
    f = struct.pack('<HHIIHH', 1, ch, rate, rate * 2 * ch, 2 * ch, 16)
    return b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVEfmt ' + struct.pack('<I', 16) + f + b'data' + struct.pack('<I', len(data)) + data

def nested(sim, fn, a4=None):
    R = list(sim.R); pend, br = sim.pend, sim.branches     # (the interrupt model keeps running: REAMP works inside a menu handler)
    sim.pend, sim.branches = [], []
    sim.R[S.ridx(1, 15)] = (R[S.ridx(1, 15)] - 0x800) & ~7
    if a4 is not None: sim.R[4] = a4
    sim.R[S.ridx(1, 3)] = 0xCAFE6000
    sim.run(fn, 0xCAFE6000, 10 ** 12)
    ret = sim.R[4]
    sim.R[:] = R; sim.pend, sim.branches = pend, br
    return ret

def run(files, plan, stop_after, rate=44100):
    sim = S.Sim(); sim.write(base, img[32:])
    for a, t in STR.items(): sim.write(a, t.encode() + b'\0')
    env = r8mock.R8Env(sim, files); env.rate = rate
    sim.wr32(HOOK, STOCK)
    st = dict(blocks=0, ours=0, ctr=0, half=0, line=[], phones=[], first=None, stock=0)
    def stock(s): st['stock'] += 1
    sim.mocks[STOCK] = stock
    def events(e):
        return 0x10020 if st['ours'] >= stop_after else 0
    env.events = events
    def block_event(s):
        if s.pend or s.branches: return s.cycle + 1
        st['half'] ^= 1; h = st['half']; s.wr32(HALF, h)
        ins = []
        for f in range(8):
            v = st['ctr']; st['ctr'] += 1; ins.append(v)
            s.write(RX + 8 * (8 * h + f), struct.pack('<ii', (v & 0x7FFFF) << 8, (-(v & 0x7FFFF)) << 8))
        fn = s.rd32(HOOK)
        R = list(s.R); pend, br = s.pend, s.branches
        s.pend, s.branches, s.next_event = [], [], None
        s.R[S.ridx(1, 15)] = 0xC0E00000; s.R[S.ridx(1, 3)] = 0xCAFE7000
        if fn == STOCK: stock(s)
        else:
            if st['first'] is None: st['first'] = ins[0]
            st['ours'] += 1
            s.run(fn, 0xCAFE7000, 10 ** 12)
            words = struct.unpack('<32i', s.read(TX + 16 * 8 * h, 128))
            for f in range(8):
                st['line'].append((words[4 * f + 1] >> 8, words[4 * f + 3] >> 8))
                st['phones'].append(((words[4 * f] >> 8) - ins[f], (words[4 * f + 2] >> 8) + ins[f]))
        s.R[:] = R; s.pend, s.branches = pend, br
        st['blocks'] += 1
        if os.environ.get('V') and st['blocks'] % 100 == 0: print('   blk', st['blocks'], 'ours', st['ours'], 'cycle', s.cycle, flush=True)
        return s.cycle + PERIOD
    sim.next_event = PERIOD; sim.on_event = block_event
    def menu(s):
        d = struct.unpack('<12I', sim.read(s.arg(0), 48))
        recs = struct.unpack('<%dI' % d[0], sim.read(d[8], 4 * d[0]))
        for row in (plan.pop(0) if plan else []):
            sim.write(d[5], struct.pack('<I', row))
            nested(sim, struct.unpack('<I', sim.read(recs[row] + 16, 4))[0])
        s.R[4] = 0
    sim.mocks[r8mock.ADDR['r8_menu_engine']] = menu
    sim.call(entry, (0, 0), max_cycles=10 ** 11)
    pops = [x[1] for x in env.log if x[0] == 'popup']
    return sim, env, st, pops

STOP_AFTER = NSRC // 8 + 200                        # blocks: the track, then 200 blocks of tail
sim, env, st, pops = run({'A:\\TRACK01.WAV': wav16(src)}, [[0], [0]], STOP_AFTER)
print('  blocks ours %d, popups %s, files %s' % (st['ours'], pops, sorted(env.fs)))
check('the per-block pointer is put back after; popup SAVED (no xruns)', sim.rd32(HOOK) == STOCK and pops == ['SAVED'] and not env.fds)
line = st['line']
want = [((1000 + i) << 8 >> 8, (-1000 - i)) for i in range(NSRC)]
exp = [(int(round((1000 + i) / 32768 * 8388608)), int(round((-1000 - i) / 32768 * 8388608))) for i in range(NSRC)]
check('line out: the source in order from the first block (16-bit scaled to 24), then silence',
      line[:NSRC] == exp and all(x == (0, 0) for x in line[NSRC:]) and len(line) == 8 * st['ours'])
check('headphones: the input of the same frame, both channels', all(p == (0, 0) for p in st['phones']))
f = bytes(env.fs.get('A:\\TRACK01 REAMP.WAV', b''))
ok = f[:4] == b'RIFF' and struct.unpack_from('<HHIIHH', f, 20) == (1, 2, 44100, 264600, 6, 24)
n = struct.unpack_from('<I', f, 40)[0] // 6 if ok else 0
rec = [(int.from_bytes(f[44 + 6 * i:47 + 6 * i], 'little', signed=True), int.from_bytes(f[47 + 6 * i:50 + 6 * i], 'little', signed=True)) for i in range(n)]
first = st['first']
expect = [(((first + 47 + i) & 0x7FFFF), -((first + 47 + i) & 0x7FFFF)) for i in range(8 * st['ours'] - 47)]
print('  recording %d frames (blocks %d x 8 - 47 = %d), RIFF size %s, file bytes %d' % (n, st['ours'], 8 * st['ours'] - 47, struct.unpack_from('<I', f, 4) if ok else None, len(f)))
check('"TRACK01 REAMP.WAV": 24-bit stereo, the input from frame 47 of the first block on, past the end of the track until STOP; header has the real length',
      ok and rec == expect and n > NSRC and struct.unpack_from('<I', f, 4)[0] == 36 + 6 * n and len(f) == 44 + 6 * n)
sim, env, st, pops = run({'A:\\TRACK01.WAV': wav16(src, rate=48000)}, [[0], [0]], 10)
check('a source at another rate: popup RATE 48000 NOT 44100, nothing written, audio untouched',
      pops == ['RATE 48000 NOT 44100'] and set(env.fs) == {'A:\\TRACK01.WAV'} and st['ours'] == 0)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
