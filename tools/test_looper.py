#!/usr/bin/env python3
"""R8 Forth app looper (cbuild/forth/looper.c, fat.c) in sim674 with interrupts: an SD card holding a FAT32 volume
with ZOOM_R8/LOOP (WAVs in several formats, plus files that must be skipped), LOOPER loads the first four by name,
timer 1 interrupts keep the McASP write FIFO fed, faders (Forth FADER! and the panel UART) set speed and level.
Checks pitch = speed x the loop's pitch, levels, channels, rate conversion, no underruns while Forth and the SD card
are busy, and a clean stop. Usage: python3 test_looper.py [outdir]"""
import os, sys, math, struct, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
from fadertab import gain as fg          # the fader law: 100 = 0 dB, 127 = +12 dB
W = os.path.join(HERE, '..')
OUT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('TMPDIR', '/tmp')
K = 4                                                     # TSCL/audio/timer 4x fast
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

def wav(rate, ch, bits, frames, fn):
    """fn(i) -> tuple of ch floats in -1..1"""
    data = bytearray()
    for i in range(frames):
        for x in fn(i):
            v = int(round(x * (2 ** (bits - 1) - 1)))
            data += v.to_bytes(bits // 8, 'little', signed=True)
    fmt = struct.pack('<HHIIHH', 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
    return b'RIFF' + struct.pack('<I', 4 + 8 + len(fmt) + 8 + len(data)) + b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + \
           b'LIST' + struct.pack('<I', 4) + b'INFO' + b'data' + struct.pack('<I', len(data)) + bytes(data)
sin = lambda f, r, i: math.sin(2 * math.pi * f * i / r)
F = fat32img.Fat32()
F.mkdir('ZOOM_R8'); F.mkdir('ZOOM_R8/LOOP'); F.mkdir('ZOOM_R8/LOOP/old')
F.add('ZOOM_R8/LOOP/._Alpha.wav', b'\0\5\26\7' + bytes(200))                      # macOS AppleDouble: skipped
F.add('ZOOM_R8/LOOP/notes.txt', b'not audio')
F.deleted('ZOOM_R8/LOOP')
F.add('ZOOM_R8/LOOP/e-extra.wav', wav(44100, 1, 16, 4410, lambda i: (0.5 * sin(3000, 44100, i),)))   # 5th: not loaded
F.add('ZOOM_R8/LOOP/Alpha.wav', wav(44100, 1, 16, 4410, lambda i: (0.5 * sin(1000, 44100, i),)), fragment=True)
F.add('ZOOM_R8/LOOP/beta 24.wav', wav(48000, 2, 24, 4800, lambda i: (0.5 * sin(600, 48000, i), 0.25 * sin(1500, 48000, i))), fragment=True)
F.add('ZOOM_R8/LOOP/C.WAV', wav(44100, 2, 16, 4410, lambda i: (0.4 * sin(500, 44100, i),) * 2))
F.add('ZOOM_R8/LOOP/d-loop.wav', wav(22050, 1, 16, 2205, lambda i: (0.5 * sin(2000, 22050, i),)))
blocks, nblk = F.image()

sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'sampler', 'out', 'sampler.out'))
B = r8board.Board(sim, clock=True, time_scale=K)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
U = r8usb.Musb(sim, B)
sim.mocks[syms['delay_us']] = lambda s: None
B.panel_pos = [95, 0, 95, 0, 0, 64, 95, 0, 100]       # before LOOPER: speeds 1x except loop 3 at the bottom (-4x, level 64)
res = {}
U.start(r8usb.mac_enumeration(res))
out, mark = {}, {}
def talk(text, wait_for=' ok', cycles=400_000_000):
    n0 = len(U.in_data); U.out_q += text.encode('latin1')
    deadline = sim.cycle + cycles
    while wait_for.encode() not in U.in_data[n0:] and sim.cycle < deadline: yield
    return U.in_data[n0:].decode('latin1')
def wait_s(sec):                                          # simulated seconds
    end = sim.cycle + sec * B.cpu_hz
    while sim.cycle < end: yield
import re
FONT = [bytes(int(x, 0) for x in m.group(1).split(',')) for m in re.finditer(r'\{([^{}]*)\}', open(os.path.join(W, 'cbuild', 'forth', 'font.h')).read().split('FONT6', 1)[1])]
INV = {g: chr(32 + i) for i, g in enumerate(FONT) if len(g) == 6}
def lcd_rows():                                           # the screen as text (6-pixel cells from column 0)
    return [''.join(INV.get(bytes(B.lcd[r * 128 + c: r * 128 + c + 6]), '#') for c in range(0, 126, 6)) for r in range(8)]
def flow():
    deadline = sim.cycle + 3_000_000_000
    while not (res.get('done') or U.errors) and sim.cycle < deadline: yield
    while b'Ctrl-C stops a running word' not in U.in_data and sim.cycle < deadline: yield
    out['looper'] = yield from talk('LOOPER\r', 'playing')
    yield from talk('\r')
    yield from wait_s(0.12); mark['snap'] = len(B.samples); yield from wait_s(0.04); mark['snap_end'] = len(B.samples)
    out['lcd'] = lcd_rows()
    out['faders'] = yield from talk('FADERS\r')
    out['f0'] = yield from talk('0 6 FADER!\r')
    yield from wait_s(0.12)
    mark['start'] = len(B.samples); yield from wait_s(0.02); mark['silent_end'] = len(B.samples)
    out['f1'] = yield from talk('95 2 FADER!\r')          # loop 1 level; its speed is 1 until fader 1 moves
    yield from wait_s(0.06); mark['a'] = len(B.samples); yield from wait_s(0.05); mark['a_end'] = len(B.samples)
    out['f2'] = yield from talk('127 1 FADER!\r')         # loop 1 speed +4
    yield from wait_s(0.12); mark['b'] = len(B.samples); yield from wait_s(0.05); mark['b_end'] = len(B.samples)
    B.send(sim.cycle / B.cpu_hz + 0.001, [0xC3, 30])      # panel: fader 1 to 30 = exactly -1
    yield from wait_s(0.14); mark['c'] = len(B.samples)
    out['busy'] = yield from talk('8200 BLK DROP 8192 BLK DROP 0 BLK DROP 3 4 * .\r')   # SD + Forth during playback
    yield from wait_s(0.02); mark['c_end'] = len(B.samples)
    out['loops1'] = yield from talk('LOOPS\r')
    out['f3'] = yield from talk('0 2 FADER! 100 4 FADER! 95 3 FADER!\r')   # loop 2 (48 kHz 24-bit stereo) at 1x
    yield from wait_s(0.08); mark['d'] = len(B.samples); yield from wait_s(0.05); mark['d_end'] = len(B.samples)
    out['f4'] = yield from talk('0 4 FADER! 100 8 FADER! 95 7 FADER! 0 9 FADER!\r')   # loop 4 (22 kHz), MASTER 0
    yield from wait_s(0.13); mark['e'] = len(B.samples); yield from wait_s(0.03); mark['e_end'] = len(B.samples)
    out['f5'] = yield from talk('100 9 FADER!\r')
    yield from wait_s(0.06); mark['f'] = len(B.samples); yield from wait_s(0.05); mark['f_end'] = len(B.samples)
    out['loops2'] = yield from talk('LOOPS\r')
    # choosing a file: hold track 4's key (A6), wheel +1 +1 -1 (5/5, wraps to 1/5, back to 5/5), release
    t = sim.cycle / B.cpu_hz
    B.send(t + 0.002, [0xA6, 3]); B.send(t + 0.03, [0xD0, 1])
    yield from wait_s(0.15); mark['held'] = len(B.samples); yield from wait_s(0.03); mark['held_end'] = len(B.samples)
    yield from wait_s(0.02); out['lcd_pick5'] = lcd_rows()
    t = sim.cycle / B.cpu_hz
    B.send(t + 0.002, [0xD0, 1]); yield from wait_s(0.12); out['lcd_pick1'] = lcd_rows()
    t = sim.cycle / B.cpu_hz
    B.send(t + 0.002, [0xD0, 0x7F]); yield from wait_s(0.12); out['lcd_back'] = lcd_rows()
    t = sim.cycle / B.cpu_hz
    B.send(t + 0.002, [0xA6, 4])                          # release: load e-extra.wav (3 kHz) into track 4
    yield from wait_s(0.2); mark['new'] = len(B.samples); yield from wait_s(0.05); mark['new_end'] = len(B.samples)
    out['lcd_new'] = lcd_rows()
    out['files'] = yield from talk('LOOPFILES\r')
    out['ll'] = yield from talk('2 1 LOAD-LOOP\r')
    mark['stop'] = len(B.samples)
    out['stop'] = yield from talk('LOOPER-STOP\r')
    mark['stopped'] = len(B.samples); out['ints_at_stop'] = sim.ints_taken
    yield from wait_s(0.01)
    out['ints_after'] = sim.ints_taken
    out['ai'] = yield from talk('AUDIO-INIT .\r')
    out['done'] = True
G = flow()
class Stop(Exception): pass
n_tr = [0]
def tr(s, pc, pk):
    U.tick()
    n_tr[0] += 1
    if n_tr[0] & 15 == 0:
        try: next(G)
        except StopIteration: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
t = time.time()
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 13)
except Stop: pass
print(f'  run: {sim.cycle:,} cycles ({sim.cycle / B.cpu_hz:.2f} simulated s) in {time.time() - t:.0f} s; '
      f'{sim.ints_taken} interrupts, timer ticks {B.tmr_ticks}; host errors: {U.errors}')
for k in ('looper', 'loops1', 'loops2', 'busy', 'stop', 'ai'): print(f'  {k}: {out.get(k, "")!r}')

def s24(v): v = (v >> 8) & 0xFFFFFF; return (v - (1 << 24) if v & 0x800000 else v) / 8388607
def chan(a, b, c):                                        # channel c (0 = left) of slots a..b
    a += a & 1
    return [s24(v) for cy, v in B.samples[a + c:b:2]]
def tone(x, f, sr=44100):
    if not x: return 0
    w = 2 * math.pi * f / sr; cc = 2 * math.cos(w); s1 = s2 = 0.0
    for v in x: s1, s2 = v + cc * s1 - s2, s1
    return 2 * math.sqrt(max(0.0, s1 * s1 + s2 * s2 - cc * s1 * s2)) / len(x)
def rms(x): return math.sqrt(sum(v * v for v in x) / max(1, len(x)))
def win(k): return mark.get(k, 0), mark.get(k + '_end', 0)
print('  LCD:'); [print('   |' + r + '|') for r in out.get('lcd', [])]
sn = chan(*win('snap'), 0); g3 = 0.4 * fg(64) * fg(100)
check(f'faders read from the panel snapshot at start: loop 3 at -4x = 2 kHz, level 64, master 100 ({tone(sn, 2000):.4f}, exp {g3:.4f})',
      abs(tone(sn, 2000) - g3) < 0.02 * g3)
lcd = out.get('lcd', [''] * 8)
check('LCD shows loop names and speeds (+1.00x, -4.00x)', lcd[0].startswith('1 Alpha.wav') and lcd[4].startswith('3 C.WAV')
      and '+1.00x' in lcd[1] and '-4.00x' in lcd[5] and lcd[6].startswith('4 d-loop.wav'))
check('FADERS prints the panel positions', ' 95 0 95 0 0 64 95 0 100  ok' in out.get('faders', ''))
lo = out.get('looper', '')
check('LOOPER loads the first four WAVs by name (skips ._ file, txt, folder, deleted entry, 5th file)',
      all(s in lo for s in ('Alpha.wav', 'beta 24.wav', 'C.WAV', 'd-loop.wav', '4 loops playing')) and 'e-extra' not in lo and '._' not in lo)
check('formats reported: 0.1 s each, 44100 mono / 48000 stereo / 44100 stereo / 22050 mono',
      all(s in lo for s in ('Alpha.wav: 0.1 s, 44100 Hz mono', 'beta 24.wav: 0.1 s, 48000 Hz stereo', 'C.WAV: 0.1 s, 44100 Hz stereo', 'd-loop.wav: 0.1 s, 22050 Hz mono')))
check(f'silent until a level fader moves (rms {rms(chan(mark.get("start", 0), mark.get("silent_end", 0), 0)):.2e})',
      mark.get('silent_end', 0) - mark.get('start', 0) > 1000 and rms(chan(mark.get('start', 0), mark.get('silent_end', 0), 0)) < 1e-6)
g1 = 0.5 * fg(95) * fg(100)
a = chan(*win('a'), 0); a1 = tone(a, 1000)
check(f'loop 1 at speed 1: 1 kHz at level fader 95, master 100 (amplitude {a1:.4f}, expected {g1:.4f}), same on right',
      len(a) > 1500 and abs(a1 - g1) < 0.01 * g1 and abs(tone(chan(*win('a'), 1), 1000) - g1) < 0.01 * g1)
b = chan(*win('b'), 0)
check(f'fader 1 to the top: speed +4 -> 4 kHz ({tone(b, 4000):.4f}; 1 kHz left {tone(b, 1000):.5f})', abs(tone(b, 4000) - g1) < 0.03 * g1 and tone(b, 1000) < 0.01 * g1)
c = chan(mark.get('c', 0), mark.get('c_end', 0), 0)
check(f'panel fader 1 to 30 (via UART): speed -1 -> 1 kHz again ({tone(c, 1000):.4f})', abs(tone(c, 1000) - g1) < 0.01 * g1 and tone(c, 4000) < 0.01 * g1)
check('LOOPS reports speed% -100 for loop 1 and the faders', '1Alpha.wav speed% -100 level 95' in out.get('loops1', '').replace(' ', '').replace('speed%', ' speed% ').replace('level', ' level ') or 'speed% -100' in out.get('loops1', ''))
check('Forth and SD reads work during playback', ' 12  ok' in out.get('busy', ''))
d = win('d'); dl, dr = chan(*d, 0), chan(*d, 1); g2 = fg(100) * fg(100)                                      # level 100 and MASTER 100: 0 dB
check(f'loop 2 (48 kHz 24-bit stereo) at 1x: left 600 Hz {tone(dl, 600):.4f} (exp {0.5 * g2:.4f}), right 1500 Hz {tone(dr, 1500):.4f} (exp {0.25 * g2:.4f}), no leakage',
      abs(tone(dl, 600) - 0.5 * g2) < 0.01 * g2 and abs(tone(dr, 1500) - 0.25 * g2) < 0.01 * g2 and tone(dl, 1500) < 1e-3 and tone(dr, 600) < 1e-3)
check(f'loop 1 silent after its level fader went to 0 ({tone(dl, 1000):.5f})', tone(dl, 1000) < 1e-3)
e = chan(*win('e'), 0)
check(f'MASTER 0 silences everything (rms {rms(e):.2e})', len(e) > 500 and rms(e) < 1e-6)
f = chan(*win('f'), 0)
check(f'loop 4 (22.05 kHz file) at 1x after MASTER back up: 2 kHz {tone(f, 2000):.4f} (exp {0.5 * g2:.4f})', abs(tone(f, 2000) - 0.5 * g2) < 0.01 * g2)
check(f'no underruns in the whole run (model: {B.late} late slots, firmware: {out.get("loops2", "").split("underruns")[-1].split()[0:1]})',
      B.late == 0 and 'underruns 0 ' in out.get('loops2', ''))
l2 = out.get('loops2', '').split()
ips = int(l2[l2.index('ints/s') + 1]) if 'ints/s' in l2 else 0
cpu = int(l2[l2.index('isr-cpu%') + 1]) if 'isr-cpu%' in l2 else 99
check(f'interrupt rate ~8000/s ({ips}), ISR CPU {cpu}% at {K}x compressed time', 7800 < ips < 8200 and cpu < 60)
tail = chan(mark.get('stop', 0), mark.get('stopped', 0), 0)
check(f'LOOPER-STOP fades out (last 200 frames rms {rms(tail[-200:]):.2e}, before {rms(tail[:500]):.3f})', rms(tail[:500]) > 0.05 and rms(tail[-200:]) < 1e-3)
check('interrupts stop after LOOPER-STOP, AUDIO-INIT works again',
      out.get('ints_after') == out.get('ints_at_stop') and out.get('ai', '').endswith('-1  ok\r\n'))
check('no USB protocol errors, FIFO never overflowed', not U.errors and B.fifo_overflows == 0)
h = chan(*win('held'), 0)
check(f'holding track 4\'s key silences it (rms {rms(h):.2e})', len(h) > 500 and rms(h) < 1e-4)
for k, want in (('lcd_pick5', ('>e-extra.wav', 'pick 5/5')), ('lcd_pick1', ('>Alpha.wav', 'pick 1/5')), ('lcd_back', ('>e-extra.wav', 'pick 5/5'))):
    rows = out.get(k, [''] * 8)
    check(f'LCD while choosing ({k}): {rows[6].strip()!r} / {rows[7].strip()!r}', rows[6].replace(' ', '').startswith('4' + want[0].replace(' ', '')) and want[1] in rows[7])
nw = chan(*win('new'), 0)
check(f'released: e-extra.wav (3 kHz) plays on track 4 at its fader level ({tone(nw, 3000):.4f}, exp {0.5 * g2:.4f}; old 2 kHz {tone(nw, 2000):.5f})',
      abs(tone(nw, 3000) - 0.5 * g2) < 0.02 * g2 and tone(nw, 2000) < 1e-3)
check('LCD shows the new file on track 4', out.get('lcd_new', [''] * 8)[6].startswith('4 e-extra.wav'))
check('LOOPFILES lists 5 files and marks e-extra.wav as track 4', '5 e-extra.wav  <- track 4' in out.get('files', ''))
check('LOAD-LOOP from Forth (file 2 into track 1)', 'loaded' in out.get('ll', ''))
B.wav(os.path.join(OUT, 'looper_sim.wav'))
print('  wrote', os.path.join(OUT, 'looper_sim.wav'))
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
