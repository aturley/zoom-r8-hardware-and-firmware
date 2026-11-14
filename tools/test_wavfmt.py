#!/usr/bin/env python3
"""WAV formats in the looper (looper.c wav_parse): 32-bit float stereo (format 3, as exported by many editors, with
fact/PEAK chunks), WAVE_FORMAT_EXTENSIBLE float, and 32-bit integer PCM load and play at the right pitch, level and
channels; an unsupported format (8-bit) is refused and the reason is shown on the LCD for that track."""
import os, sys, math, time, struct, re
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
W = os.path.join(HERE, '..')
K = 4
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
def chunk(cid, data): return cid + struct.pack('<I', len(data)) + data + (b'\0' if len(data) & 1 else b'')
def wav(fmt_chunk, data, extra=b''): body = b'WAVE' + chunk(b'fmt ', fmt_chunk) + extra + chunk(b'data', data); return b'RIFF' + struct.pack('<I', len(body)) + body
FR = 4410
def frames(f_l, f_r, amp=0.5): return [(amp * math.sin(2 * math.pi * f_l * i / 44100), amp * math.sin(2 * math.pi * f_r * i / 44100)) for i in range(FR)]
fl = frames(500, 1500)
f32 = wav(struct.pack('<HHIIHH', 3, 2, 44100, 44100 * 8, 8, 32), b''.join(struct.pack('<ff', a, b) for a, b in fl),
          chunk(b'fact', struct.pack('<I', FR)) + chunk(b'PEAK', bytes(24)))
GUID_FLOAT = bytes.fromhex('0300000000001000800000aa00389b71')
ext = wav(struct.pack('<HHIIHH', 0xFFFE, 2, 44100, 44100 * 8, 8, 32) + struct.pack('<HHI', 22, 32, 3) + GUID_FLOAT,
          b''.join(struct.pack('<ff', a, b) for a, b in frames(700, 2100)))
i32 = wav(struct.pack('<HHIIHH', 1, 1, 44100, 44100 * 4, 4, 32), b''.join(struct.pack('<i', int(a * 2147483647)) for a, b in frames(900, 900)))
u8 = wav(struct.pack('<HHIIHH', 1, 1, 44100, 44100, 1, 8), bytes(128 + int(60 * math.sin(i)) for i in range(FR)))
F = fat32img.Fat32(); F.mkdir('ZOOM_R8'); F.mkdir('ZOOM_R8/LOOP')
F.add('ZOOM_R8/LOOP/a float.wav', f32); F.add('ZOOM_R8/LOOP/b ext.wav', ext); F.add('ZOOM_R8/LOOP/c int32.wav', i32); F.add('ZOOM_R8/LOOP/d u8.wav', u8)
blocks, nblk = F.image()
sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'sampler', 'out', 'sampler.out'))
B = r8board.Board(sim, clock=True, time_scale=K)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
U = r8usb.Musb(sim, B)
sim.mocks[syms['delay_us']] = lambda s: None
B.panel_pos = [95, 0, 95, 0, 95, 0, 95, 0, 100]
res = {}; U.start(r8usb.mac_enumeration(res))
out, mark = {}, {}
def talk(text, wait_for=' ok', cycles=600_000_000):
    n0 = len(U.in_data); U.out_q += text.encode('latin1')
    deadline = sim.cycle + cycles
    while wait_for.encode() not in U.in_data[n0:] and sim.cycle < deadline: yield
    return U.in_data[n0:].decode('latin1')
def wait_s(sec):
    end = sim.cycle + sec * B.cpu_hz
    while sim.cycle < end: yield
import re as _re
FONT = [bytes(int(x, 0) for x in m.group(1).split(',')) for m in _re.finditer(r'\{([^{}]*)\}', open(os.path.join(W, 'cbuild', 'forth', 'font.h')).read().split('FONT6', 1)[1])]
INV = {g: chr(32 + i) for i, g in enumerate(FONT) if len(g) == 6}
def lcd_rows(): return [''.join(INV.get(bytes(B.lcd[r * 128 + c: r * 128 + c + 6]), '#') for c in range(0, 126, 6)) for r in range(8)]
def flow():
    deadline = sim.cycle + 3_000_000_000
    while not (res.get('done') or U.errors) and sim.cycle < deadline: yield
    while b'Ctrl-C stops a running word' not in U.in_data and sim.cycle < deadline: yield
    out['looper'] = yield from talk('LOOPER\r', 'playing')
    yield from wait_s(0.1)                                 # the panel's start-up snapshot (levels 0) lands first
    for t in (1, 2, 3):
        yield from talk(f'100 {2 * t} FADER!\r')
        yield from wait_s(0.1); mark[t] = len(B.samples); yield from wait_s(0.04); mark[f'{t}e'] = len(B.samples)
        yield from talk(f'0 {2 * t} FADER!\r')
    yield from talk('35 9 FADER! 100 2 FADER!\r')       # MASTER -20 dB; track 1 at 0 dB, then +12 dB
    yield from wait_s(0.12); mark['u'] = len(B.samples); yield from wait_s(0.04); mark['ue'] = len(B.samples)
    yield from talk('127 2 FADER!\r')
    yield from wait_s(0.12); mark['t'] = len(B.samples); yield from wait_s(0.04); mark['te'] = len(B.samples)
    yield from talk('0 2 FADER! 100 9 FADER!\r')
    out['load4'] = yield from talk('4 4 LOAD-LOOP\r')
    yield from wait_s(0.25); out['lcd'] = lcd_rows()
    out['done'] = True
G = flow()
class Stop(Exception): pass
n_tr = [0]
def tr(s, pc, pk):
    U.tick(); n_tr[0] += 1
    if n_tr[0] & 15 == 0:
        try: next(G)
        except StopIteration: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
t0 = time.time()
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 13)
except Stop: pass
print(f'  run: {sim.cycle:,} cycles in {time.time() - t0:.0f} s; host errors: {U.errors}')
print('  looper:', repr(out.get('looper', ''))); print('  load4:', repr(out.get('load4', '')))
print('  LCD:'); [print('   |' + r + '|') for r in out.get('lcd', [])]
def s24(v): v = (v >> 8) & 0xFFFFFF; return (v - (1 << 24) if v & 0x800000 else v) / 8388607
def chan(a, b, c): a += a & 1; return [s24(v) for cy, v in B.samples[a + c:b:2]]
def tone(x, f, sr=44100):
    if not x: return 0
    w = 2 * math.pi * f / sr; cc = 2 * math.cos(w); s1 = s2 = 0.0
    for v in x: s1, s2 = v + cc * s1 - s2, s1
    return 2 * math.sqrt(max(0.0, s1 * s1 + s2 * s2 - cc * s1 * s2)) / len(x)
lp = out.get('looper', '')
check('the float, extensible and int32 files load; the 8-bit one is refused', '3 loops playing' in lp and 'bits not 16/24/32' in lp)
for t, name, fl_, fr_ in ((1, '32-bit float stereo (fact, PEAK chunks)', 500, 1500), (2, 'extensible float stereo', 700, 2100), (3, '32-bit integer mono', 900, 900)):
    L, R = chan(mark.get(t, 0), mark.get(f'{t}e', 0), 0), chan(mark.get(t, 0), mark.get(f'{t}e', 0), 1)
    check(f'{name}: left {fl_} Hz {tone(L, fl_):.3f}, right {fr_} Hz {tone(R, fr_):.3f} (0.5 each)', len(L) > 500 and abs(tone(L, fl_) - 0.5) < 0.03 and abs(tone(R, fr_) - 0.5) < 0.03)
u, t_ = tone(chan(mark.get('u', 0), mark.get('ue', 0), 0), 500), tone(chan(mark.get('t', 0), mark.get('te', 0), 0), 500)
d = 20 * math.log10(t_ / u) if u and t_ else 0
check(f'level fader 127 is +12 dB over 100 ({d:.2f} dB; 100 with MASTER 35 = {u:.4f}, i.e. 0.5 x -20 dB)', abs(d - 12.0) < 0.1 and abs(u - 0.05) < 0.002)
lcd = out.get('lcd', [''] * 8)
check('a failed load shows why on the LCD (track 4: "bits not 16/24/32")', 'failed' in out.get('load4', '') and lcd[6].startswith('4 -') and 'bits not 16/24/32' in lcd[7])
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
