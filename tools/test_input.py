#!/usr/bin/env python3
"""Looper audio input (cbuild/forth/looper.c: McASP receive through the AFIFO read FIFO) in sim674. The ADC model
delivers 700 Hz on the left (0.5) and 1300 Hz on the right (0.25). Checks: MONITOR mixes the input into the output
with channels the right way round and is silent at 0; REC 1 / REC-STOP records the input into track 1, which then
loops it (pitch, level, channels); no output underruns and no input overruns throughout."""
import os, sys, math, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
W = os.path.join(HERE, '..')
K = 4
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
F = fat32img.Fat32(); F.mkdir('ZOOM_R8'); F.mkdir('ZOOM_R8/LOOP')
blocks, nblk = F.image()
sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'sampler', 'out', 'sampler.out'))
B = r8board.Board(sim, clock=True, time_scale=K)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
U = r8usb.Musb(sim, B)
sim.mocks[syms['delay_us']] = lambda s: None
FS = 44100
def adc(k):                                               # slot k: even = left
    i, right = k >> 1, k & 1
    x = 0.25 * math.sin(2 * math.pi * 1300 * i / FS) if right else 0.5 * math.sin(2 * math.pi * 700 * i / FS)
    return int(round(x * 8388607)) & 0xFFFFFF
B.rx_signal = adc
B.tx_skew = int(os.environ.get('R8_TX_SKEW', '0')); B.rx_skew = int(os.environ.get('R8_RX_SKEW', '0'))   # start one slot off
print(f'  tx_skew {B.tx_skew} rx_skew {B.rx_skew}')
B.panel_pos = [95, 0, 95, 0, 95, 0, 95, 0, 100]           # speeds 1x, levels 0, MASTER full
res = {}
U.start(r8usb.mac_enumeration(res))
out, mark = {}, {}
def talk(text, wait_for=' ok', cycles=400_000_000):
    n0 = len(U.in_data); U.out_q += text.encode('latin1')
    deadline = sim.cycle + cycles
    while wait_for.encode() not in U.in_data[n0:] and sim.cycle < deadline: yield
    return U.in_data[n0:].decode('latin1')
def wait_s(sec):
    end = sim.cycle + sec * B.cpu_hz
    while sim.cycle < end: yield
def flow():
    deadline = sim.cycle + 3_000_000_000
    while not (res.get('done') or U.errors) and sim.cycle < deadline: yield
    while b'Ctrl-C stops a running word' not in U.in_data and sim.cycle < deadline: yield
    out['looper'] = yield from talk('LOOPER\r', 'playing')
    yield from wait_s(0.05)
    out['in1'] = yield from talk('INPUT\r')
    mark['quiet'] = len(B.samples); yield from wait_s(0.03); mark['quiet_end'] = len(B.samples)
    out['mon'] = yield from talk('127 MONITOR\r')
    yield from wait_s(0.08); mark['mon'] = len(B.samples); yield from wait_s(0.05); mark['mon_end'] = len(B.samples)
    out['mon0'] = yield from talk('0 MONITOR\r')
    yield from wait_s(0.16); mark['off'] = len(B.samples); yield from wait_s(0.03); mark['off_end'] = len(B.samples)
    out['rec'] = yield from talk('1 REC\r')
    yield from wait_s(0.1)
    out['loops_rec'] = yield from talk('LOOPS\r')
    out['stop'] = yield from talk('REC-STOP\r')
    out['lvl'] = yield from talk('100 2 FADER!\r')
    yield from wait_s(0.08); mark['play'] = len(B.samples); yield from wait_s(0.05); mark['play_end'] = len(B.samples)
    out['loops'] = yield from talk('LOOPS\r')
    out['in2'] = yield from talk('INPUT\r')
    # panel: REC (arm), track 2's key starts recording, ~0.1 s, track 2's key again stops it
    def key(st, sec=0.001): B.send(sim.cycle / B.cpu_hz + sec, [st, 3]); B.send(sim.cycle / B.cpu_hz + sec + 0.01, [st, 4])
    tx0 = len(B.tx)
    key(0xA2); yield from wait_s(0.55)                    # armed > 2 half-periods (0.25 s each): both LED edges, whatever the phase
    out['led_armed'] = bytes(B.tx[tx0:])
    key(0xA4); yield from wait_s(0.05)
    out['loops_prec'] = yield from talk('LOOPS\r')
    yield from wait_s(0.05)
    key(0xA4); yield from wait_s(0.05)
    out['loops_pdone'] = yield from talk('LOOPS\r')
    out['led_all'] = bytes(B.tx[tx0:])
    # REC twice = disarmed: track 3's key then selects (nothing records)
    key(0xA2); yield from wait_s(0.05); key(0xA2); yield from wait_s(0.05)
    key(0xA5); yield from wait_s(0.05)
    out['loops_disarm'] = yield from talk('LOOPS\r')
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
print(f'  run: {sim.cycle:,} cycles ({sim.cycle / B.cpu_hz:.2f} simulated s) in {time.time() - t:.0f} s; host errors: {U.errors}')
for k in ('looper', 'in1', 'rec', 'loops_rec', 'stop', 'loops', 'in2'): print(f'  {k}: {out.get(k, "")!r}')
def s24(v): v = (v >> 8) & 0xFFFFFF; return (v - (1 << 24) if v & 0x800000 else v) / 8388607
def chan(a, b, c):
    a += a & 1
    return [s24(v) for cy, v in B.samples[a + c:b:2]]
def tone(x, f, sr=FS):
    if not x: return 0
    w = 2 * math.pi * f / sr; cc = 2 * math.cos(w); s1 = s2 = 0.0
    for v in x: s1, s2 = v + cc * s1 - s2, s1
    return 2 * math.sqrt(max(0.0, s1 * s1 + s2 * s2 - cc * s1 * s2)) / len(x)
def rms(x): return math.sqrt(sum(v * v for v in x) / max(1, len(x)))
def win(k): return mark.get(k, 0), mark.get(k + '_end', 0)
import re
def num(key, txt): m = re.search(key + r' (-?\d+)', txt); return int(m.group(1)) if m else None
check('LOOPER starts with an empty folder and the input runs', '0 loops playing' in out.get('looper', '') and (num('frames', out.get('in1', '')) or 0) > 1000)
q = chan(*win('quiet'), 0)
check(f'input not heard before MONITOR (rms {rms(q):.1e})', len(q) > 500 and rms(q) < 1e-6)
L, R = chan(*win('mon'), 0), chan(*win('mon'), 1)
check(f'MONITOR 127: left 700 Hz at 0.5 ({tone(L, 700):.4f}), right 1300 Hz at 0.25 ({tone(R, 1300):.4f})',
      len(L) > 1000 and abs(tone(L, 700) - 0.5) < 0.01 and abs(tone(R, 1300) - 0.25) < 0.005)
check(f'channels not swapped (left 1300 Hz {tone(L, 1300):.1e}, right 700 Hz {tone(R, 700):.1e})', tone(L, 1300) < 1e-3 and tone(R, 700) < 1e-3)
o = chan(*win('off'), 0)
check(f'MONITOR 0: silent again (rms {rms(o):.1e})', len(o) > 500 and rms(o) < 1e-5)
n = num('', out.get('stop', '').replace('REC-STOP', '').strip().split('frames')[0].strip() and ' ' + out.get('stop', '').replace('REC-STOP', '').strip().split('frames')[0].strip())
fr = int(re.findall(r'(\d+)\s+frames', out.get('stop', ''))[0]) if re.findall(r'(\d+)\s+frames', out.get('stop', '')) else 0
check(f'REC 1 shows as recording; REC-STOP recorded about 0.1 s ({fr} frames)', 'recording' in out.get('loops_rec', '') and 3500 < fr < 5500)
import struct
rec = sim.read(0xC0400000 + 512, 4 * fr) if fr else b''          # samples start 512 bytes in (WAV header room)
rl = [struct.unpack_from('<h', rec, 4 * i)[0] / 32767 for i in range(fr)]
rr = [struct.unpack_from('<h', rec, 4 * i + 2)[0] / 32767 for i in range(fr)]
check(f'the recording holds the input: left 700 Hz ({tone(rl, 700):.4f}), right 1300 Hz ({tone(rr, 1300):.4f})',
      fr and abs(tone(rl, 700) - 0.5) < 0.002 and abs(tone(rr, 1300) - 0.25) < 0.002 and tone(rl, 1300) < 0.01 and tone(rr, 700) < 0.01)
L, R = chan(*win('play'), 0), chan(*win('play'), 1)
# the loop is not a whole number of cycles, so a tone detector reads low across a wrap: compare levels instead
check(f'track 1 loops it at full level: rms left {rms(L):.4f} (exp 0.354), right {rms(R):.4f} (exp 0.177)',
      len(L) > 1000 and abs(rms(L) - 0.5 / 2 ** 0.5) < 0.01 and abs(rms(R) - 0.25 / 2 ** 0.5) < 0.006)
check('LOOPS lists track 1 as REC 1 at speed 100%', 'REC 1 speed% 100' in out.get('loops', ''))
check(f'no output underruns ({num("underruns", out.get("loops", ""))}), no input overruns ({num("overruns", out.get("in2", ""))})',
      num('underruns', out.get('loops', '')) == 0 and num('overruns', out.get('in2', '')) == 0)
for k in ('loops_prec', 'loops_pdone', 'loops_disarm'): print(f'  {k}: {out.get(k, "")!r}')
LED_ON = bytes([0x88, 1, 8, 0, 0, 0, 0, 4, 0, 0, 0, 0xF7])   # 88 01 08 b0..b7 F7; REC = b4 bit 2
LED_OFF = bytes([0x88, 1, 8] + [0] * 8 + [0xF7])
la = out.get('led_armed', b'')
check(f'REC arms: the REC LED blinks ({la.count(LED_ON)} on, {la.count(LED_OFF)} off messages)', la.count(LED_ON) >= 1 and la.count(LED_OFF) >= 1)
check('then track 2\'s key starts recording track 2', '2 recording' in out.get('loops_prec', ''))
m = re.search(r'2 REC 2 speed% 100', out.get('loops_pdone', ''))
check('track 2\'s key again stops it: track 2 loops "REC 2"', bool(m) and 'recording' not in out.get('loops_pdone', ''))
al = out.get('led_all', b'')
check('REC LED steady on while recording, off afterwards', al.rfind(LED_OFF) > al.rfind(LED_ON) > 0)
check('REC pressed twice disarms: track 3\'s key records nothing', 'recording' not in out.get('loops_disarm', '') and '3 -' in out.get('loops_disarm', ''))
m = re.search(r'align tx (\d+)\s+rx (\d+)', out.get('in2', ''))
print(f'  realignments: {m.groups() if m else None}')
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
