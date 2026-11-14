#!/usr/bin/env python3
"""Recording the mix (looper.c, track status keys): track 1 plays a 1 kHz loop at +4x (4 kHz) at full level, MASTER
low. Pressing the status key above fader 3 records the mix of the other tracks into track 2; the key above fader 4
(the same track's other key) stops it. The recording must hold 4 kHz at the track's level, unaffected by MASTER and
the input; track 2 then loops it; both of track 2's status keys light red while recording, and go dark after."""
import os, sys, math, time, struct, re
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
W = os.path.join(HERE, '..')
K = 4
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
def wavfile(rate, frames, f, amp):
    data = b''.join(struct.pack('<h', int(amp * 32767 * math.sin(2 * math.pi * f * i / rate))) for i in range(frames))
    fmt = struct.pack('<HHIIHH', 1, 1, rate, rate * 2, 2, 16)
    return b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVEfmt ' + struct.pack('<I', 16) + fmt + b'data' + struct.pack('<I', len(data)) + data
F = fat32img.Fat32(); F.mkdir('ZOOM_R8'); F.mkdir('ZOOM_R8/LOOP')
F.add('ZOOM_R8/LOOP/a.wav', wavfile(44100, 4410, 1000, 0.5))
blocks, nblk = F.image()
sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'sampler', 'out', 'sampler.out'))
B = r8board.Board(sim, clock=True, time_scale=K)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
U = r8usb.Musb(sim, B)
sim.mocks[syms['delay_us']] = lambda s: None
B.rx_signal = lambda k: int(0.4 * 8388607 * math.sin(2 * math.pi * 300 * (k >> 1) / 44100)) & 0xFFFFFF   # input: must not be recorded
B.panel_pos = [127, 100, 95, 0, 95, 0, 95, 0, 40]       # track 1 at +4x, level 100 (0 dB); MASTER low
res = {}
U.start(r8usb.mac_enumeration(res))
out = {}
def talk(text, wait_for=' ok', cycles=600_000_000):
    n0 = len(U.in_data); U.out_q += text.encode('latin1')
    deadline = sim.cycle + cycles
    while wait_for.encode() not in U.in_data[n0:] and sim.cycle < deadline: yield
    return U.in_data[n0:].decode('latin1')
def wait_s(sec):
    end = sim.cycle + sec * B.cpu_hz
    while sim.cycle < end: yield
def key(st, sec=0.001): B.send(sim.cycle / B.cpu_hz + sec, [st, 3]); B.send(sim.cycle / B.cpu_hz + sec + 0.01, [st, 4])
def flow():
    deadline = sim.cycle + 3_000_000_000
    while not (res.get('done') or U.errors) and sim.cycle < deadline: yield
    while b'Ctrl-C stops a running word' not in U.in_data and sim.cycle < deadline: yield
    out['looper'] = yield from talk('LOOPER\r', 'playing')
    yield from talk('127 MONITOR\r')                        # the input is audible, but must not be in the recording
    yield from wait_s(0.15)
    tx0 = len(B.tx)
    key(0x91); yield from wait_s(0.15)                     # status key above fader 3 = track 2
    out['loops_rec'] = yield from talk('LOOPS\r')
    out['led_rec'] = bytes(B.tx[tx0:])
    key(0x92); yield from wait_s(0.05)                     # the other key of the pair stops it
    out['loops_done'] = yield from talk('LOOPS\r')
    out['led_all'] = bytes(B.tx[tx0:])
    out['n'] = sim.rd32(syms['lp_len'] + 4)
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
t = time.time()
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 13)
except Stop: pass
print(f'  run: {sim.cycle:,} cycles in {time.time() - t:.0f} s; host errors: {U.errors}')
for k in ('looper', 'loops_rec', 'loops_done'): print(f'  {k}: {out.get(k, "")!r}')
n = out.get('n', 0)
rec = sim.read(0xC0400000 + (0xC4000000 - 0xC0400000) // 4 + 512, 4 * n) if n else b''
L = [struct.unpack_from('<h', rec, 4 * i)[0] / 32767 for i in range(n)]
def tone(x, f, sr=44100):
    if not x: return 0
    w = 2 * math.pi * f / sr; cc = 2 * math.cos(w); s1 = s2 = 0.0
    for v in x: s1, s2 = v + cc * s1 - s2, s1
    return 2 * math.sqrt(max(0.0, s1 * s1 + s2 * s2 - cc * s1 * s2)) / len(x)
rms = lambda x: math.sqrt(sum(v * v for v in x) / max(1, len(x)))
seg = L[2000:2000 + 2205] if n > 4500 else L
check('the track 2 status key starts recording the mix into track 2', '2 recording' in out.get('loops_rec', ''))
check(f'the other key of the pair stops it; track 2 loops "REC 2" ({n} frames)', '2 REC 2' in out.get('loops_done', '') and n > 4000)
check(f'the recording is track 1 at +4x: 4 kHz at 0.5 ({tone(seg, 4000):.3f}), MASTER not applied, rms {rms(seg):.3f}',
      abs(tone(seg, 4000) - 0.5) < 0.03)
check(f'the input (300 Hz, monitored) is not in it ({tone(seg, 300):.4f})', tone(seg, 300) < 0.005)
ON = bytes([0x88, 1, 8, 0, 0, 0, 0, 1, 0, 1, 0, 0xF7])    # status keys 3 and 4 red: b4 bit 0, b6 bit 0
OFF = bytes([0x88, 1, 8] + [0] * 8 + [0xF7])
la, al = out.get('led_rec', b''), out.get('led_all', b'')
check('both of track 2\'s status keys light red while recording', ON in la)
check('and go dark when it stops', al.rfind(OFF) > al.rfind(ON) >= 0)
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
