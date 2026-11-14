#!/usr/bin/env python3
"""BLIPPOO app (cbuild/blippoo) in sim674 with the board model (r8board: LCD, panel UART, McASP FIFOs, timer 1 and
its interrupt), in real time (the DSP uses about a third of the CPU). Checks: the start
screen (bank 1 CORE, parameter names), audio on both channels with no underruns, the interrupt load, a bank-1 fader
setting its parameter, bank 2 via the second key under the screen, pickup (a fader below the value does nothing,
the parameter follows once the fader crosses it, the screen shows the arrow until then), MASTER at 0 silencing the
output, and EXIT restarting into the stock firmware. Usage: python3 test_blippoo.py [outdir]"""
import os, sys, math, struct, time, re
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import subprocess, tempfile, wave
import sim674 as S, r8board, r8sd, fat32img
W = os.path.join(HERE, '..')
OUT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('TMPDIR', '/tmp')
K = 1
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'blippoo', 'out', 'blippoo.out'))
B = r8board.Board(sim, clock=True, time_scale=K)
F = fat32img.Fat32()                                                # a card with ZOOM_R8/LOOP holding BLIP0002.WAV
F.mkdir('ZOOM_R8'); F.mkdir('ZOOM_R8/LOOP')
F.add('ZOOM_R8/LOOP/BLIP0002.WAV', bytes(1000)); F.add('ZOOM_R8/keep.txt', b'do not touch ' * 100)
blocks, nblk = F.image()
fsi = bytearray(blocks[F.part + 1]); struct.pack_into('<I', fsi, 488, sum(1 for v in F.fat[2:] if v == 0)); blocks[F.part + 1] = bytes(fsi)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
sim.mocks[syms['delay_us']] = lambda s: None
class Restart(Exception): pass
def restart(s): raise Restart
sim.mocks[syms['r8_restart']] = restart
B.panel_pos = [100, 90, 30, 20, 25, 35, 15, 30, 100]
FONT = [bytes(int(x, 0) for x in m.group(1).split(',')) for m in re.finditer(r'\{([^{}]*)\}', open(os.path.join(W, 'cbuild', 'forth', 'font.h')).read().split('FONT6', 1)[1])]
INV = {g: chr(32 + i) for i, g in enumerate(FONT) if len(g) == 6}
INVI = {bytes(x ^ 0xFF for x in g): c for g, c in INV.items()}
def lcd_rows():
    return [''.join(INV.get(bytes(B.lcd[r * 128 + c: r * 128 + c + 6]), INVI.get(bytes(B.lcd[r * 128 + c: r * 128 + c + 6]), '#'))
                    for c in range(0, 126, 6)) for r in range(8)]
fl = lambda name: struct.unpack('<f', sim.read(syms[name], 4))[0]
def value(i): return struct.unpack('<f', sim.read(syms['value'] + 4 * i, 4))[0]
def picked(i): return sim.read(syms['picked'] + i, 1)[0]
def u32(name): return sim.rd32(syms[name])
def now_s(): return sim.cycle / B.cpu_hz
def wait_s(sec):
    end = sim.cycle + sec * B.cpu_hz
    while sim.cycle < end: yield
def send(data): B.send(now_s() + 0.001, data)
out, mark = {}, {}
def flow():
    while 'CORE' not in lcd_rows()[0] or u32('bp_ints') < 50: yield
    yield from wait_s(0.02)
    out['screen1'] = lcd_rows()
    yield from wait_s(0.1)                                          # past the start-up realignment (one slot may shift)
    mark['a'] = len(B.samples); yield from wait_s(0.08); mark['a_end'] = len(B.samples)
    out['osc_a0'] = value(0)
    send([0xC3, 64]); yield from wait_s(0.01)                       # fader 1 (bank 1: OSC A) to 64
    out['osc_a1'] = value(0)
    send([0xA4, 3]); yield from wait_s(0.005); send([0xA4, 4]); yield from wait_s(0.02)   # second key under the screen
    out['bank'] = u32('bank'); out['screen2'] = lcd_rows()
    out['peak1_0'] = value(8); out['picked0'] = picked(8)
    send([0xC3, 30]); yield from wait_s(0.01)                       # PEAK 1 (default 0.55 = 70): below, no pickup
    out['peak1_1'] = value(8); out['picked1'] = picked(8); out['screen3'] = lcd_rows()
    send([0xC3, 90]); yield from wait_s(0.01)                       # crosses 70: picked up, follows
    out['peak1_2'] = value(8); out['picked2'] = picked(8)
    out['osc_a2'] = value(0)                                        # bank 1's value kept
    send([0xA2, 3]); yield from wait_s(0.005); send([0xA2, 4])     # REC: records
    while not u32('rec_p'): yield
    mark['r'] = len(B.samples); yield from wait_s(0.3)
    out['screen_rec'] = lcd_rows()
    send([0xA2, 3]); yield from wait_s(0.005); send([0xA2, 4])     # REC again: stops and saves
    mark['r_end'] = len(B.samples)
    t0 = now_s()
    while 'saved' not in lcd_rows()[5] and 'not saved' not in lcd_rows()[5] and now_s() - t0 < 3: yield
    out['save_s'] = now_s() - t0; out['screen_saved'] = lcd_rows()
    send([0xCB, 0]); yield from wait_s(0.15)                        # MASTER to 0: fades out
    mark['m'] = len(B.samples); yield from wait_s(0.02); mark['m_end'] = len(B.samples)
    out['underruns'] = u32('bp_underruns'); out['ints'] = u32('bp_ints'); out['cyc'] = u32('bp_isr_cycles'); out['max'] = u32('bp_isr_max')
    send([0x93, 3]); yield from wait_s(0.5)                         # EXIT (fades out first, up to 0.25 s)
    out['no_restart'] = True
G = flow()
class Stop(Exception): pass
n = [0]
def tr(s, pc, pk):
    n[0] += 1
    if n[0] & 15 == 0:
        try: next(G)
        except StopIteration: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
t = time.time(); restarted = False
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 12)
except Stop: pass
except Restart: restarted = True
print(f'  run: {sim.cycle:,} cycles ({sim.cycle / B.cpu_hz:.2f} simulated s) in {time.time() - t:.0f} s; {sim.ints_taken} interrupts')
for k in ('screen1', 'screen2', 'screen3', 'screen_rec', 'screen_saved'):
    print('  %s:' % k); [print('    |' + r + '|') for r in out.get(k, [])]
def s24(v): v = (v >> 8) & 0xFFFFFF; return (v - (1 << 24) if v & 0x800000 else v) / 8388607
def chan(a, b, c): a += a & 1; return [s24(v) for cy, v in B.samples[a + c:b:2]]
db = lambda x: 20 * math.log10(max(x, 1e-9))
rms = lambda x: math.sqrt(sum(v * v for v in x) / max(1, len(x)))
L, R = chan(mark.get('a', 0), mark.get('a_end', 0), 0), chan(mark.get('a', 0), mark.get('a_end', 0), 1)
print('  audio: %d frames, L rms %.1f dBFS peak %.1f, R = L: %s; after MASTER 0: rms %.1f dBFS' % (
    len(L), db(rms(L)), db(max(map(abs, L or [0]))), L == R, db(rms(chan(mark.get('m', 0), mark.get('m_end', 0), 0)))))
s1 = out.get('screen1', [''] * 8)
check('start screen: bank 1 CORE, parameter names', 'BLIPPOO' in s1[0] and 'CORE' in s1[0] and 'OSC A' in s1[1] and 'OSC B' in s1[1] and 'COLOUR' in s1[4])
check('audio on both channels (the same), not silent, below full scale', L and L == R and -60 < db(rms(L)) < -3 and max(map(abs, L)) < 1.0)
ints = out.get('ints', 1)
load = out.get('cyc', 0) / max(ints, 1) * 8000 / 360e6 * 100
print('  interrupt load %.0f%% of the CPU (max %d cycles = %.0f us), underruns %s' % (load, out.get('max', 0), out.get('max', 0) / 360, out.get('underruns')))
check('no underruns; interrupt load below 60%', out.get('underruns') == 0 and load < 60)
check('bank 1: fader 1 sets OSC A (64/127)', abs(out.get('osc_a1', 0) - 64 / 127) < 1e-4 and out.get('osc_a0') != out.get('osc_a1'))
s2 = out.get('screen2', [''] * 8)
check('the second key under the screen: bank 2 RESONATOR', out.get('bank') == 1 and 'RESONATOR' in s2[0] and 'PEAK 1' in s2[1])
check('pickup: PEAK 1 not picked, a fader below its value changes nothing, the screen shows ^',
      out.get('picked0') == 0 and out.get('picked1') == 0 and abs(out.get('peak1_1', 0) - out.get('peak1_0', 1)) < 1e-6
      and '^' in out.get('screen3', [''] * 8)[1][:10])
check('pickup: crossing the value picks it up and the parameter follows (90/127); bank 1 values kept',
      out.get('picked2') == 1 and abs(out.get('peak1_2', 0) - 90 / 127) < 1e-4 and abs(out.get('osc_a2', 0) - 64 / 127) < 1e-4)
check('MASTER at 0 fades the output out (below -90 dBFS after 150 ms)', db(rms(chan(mark.get('m', 0), mark.get('m_end', 0), 0))) < -90)
check('EXIT restarts into the stock firmware', restarted and not out.get('no_restart'))
B.wav(os.path.join(OUT, 'blippoo_sim.wav'))
sr = out.get('screen_rec', [''] * 8); ss = out.get('screen_saved', [''] * 8)
check('REC: the screen shows the recording time', 'REC 0:00' in sr[5])
check('REC again: saved as BLIP0003.WAV (after the existing BLIP0002) in %.2f s' % out.get('save_s', 0), 'saved BLIP0003.WAV' in ss[5])
check('no card protocol errors', not C.errors)
tmp = tempfile.mkdtemp(); imgp = os.path.join(tmp, 'card.img')
with open(imgp, 'wb') as f:
    f.truncate((nblk + 1024) * 512)
    for b, d in C.image.items(): f.seek(b * 512); f.write(d)
fsck_ok, wav_ok, other_ok, info, devs = False, False, False, '', []
try:
    att = subprocess.run(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nomount', imgp], capture_output=True, text=True)
    devs = [l.split()[0] for l in att.stdout.splitlines() if l.strip()]
    part = next((d for d in devs if d.endswith('s1')), None)
    if part:
        fk = subprocess.run(['fsck_msdos', '-n', part], capture_output=True, text=True)
        txt = fk.stdout + fk.stderr
        print('  fsck_msdos -n:', ' | '.join(l for l in txt.splitlines() if l.strip()))
        fsck_ok = fk.returncode == 0 and 'Fix?' not in txt and 'INCORRECT' not in txt.upper()
        mnt = os.path.join(tmp, 'mnt'); os.mkdir(mnt)
        m = subprocess.run(['mount', '-t', 'msdos', '-o', 'rdonly', part, mnt], capture_output=True, text=True)
        if m.returncode == 0:
            try:
                w = wave.open(os.path.join(mnt, 'ZOOM_R8', 'LOOP', 'BLIP0003.WAV')); n = w.getnframes(); raw = w.readframes(n)
                info = f'{w.getnchannels()} ch, {w.getframerate()} Hz, {w.getsampwidth() * 8}-bit, {n} frames'
                fr = [int.from_bytes(raw[6 * i: 6 * i + 3], 'little', signed=True) for i in range(n)]
                frr = [int.from_bytes(raw[6 * i + 3: 6 * i + 6], 'little', signed=True) for i in range(n)]
                dac = [(v >> 8) & 0xFFFFFF for cy, v in B.samples[mark['r'] - 4000: mark['r_end'] + 4000: 2]]
                dac = [v - (1 << 24) if v & 0x800000 else v for v in dac]
                k = next((i for i in range(len(dac) - n) if dac[i:i + 64] == fr[:64]), -1)
                match = k >= 0 and dac[k:k + n] == fr
                info += f'; equals the output: {match}; L == R: {fr == frr}'
                exp = (mark['r_end'] - mark['r']) // 2
                wav_ok = w.getnchannels() == 2 and w.getframerate() == 44100 and w.getsampwidth() == 3 and match and fr == frr and abs(n - exp) < 600
                info += f' (expected about {exp})'
                other_ok = open(os.path.join(mnt, 'ZOOM_R8', 'keep.txt'), 'rb').read() == b'do not touch ' * 100 and \
                           os.path.getsize(os.path.join(mnt, 'ZOOM_R8', 'LOOP', 'BLIP0002.WAV')) == 1000
            finally: subprocess.run(['umount', mnt], capture_output=True)
        else: print('  mount failed:', m.stderr.strip())
finally:
    for d in reversed(devs):
        if not d.endswith('s1'): subprocess.run(['hdiutil', 'detach', '-force', d], capture_output=True)
    os.remove(imgp)
check('macOS fsck_msdos finds the volume consistent', fsck_ok)
check(f'BLIP0003.WAV: 24-bit stereo 44.1 kHz, sample for sample what went out ({info})', wav_ok)
check('other files untouched', other_ok)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
