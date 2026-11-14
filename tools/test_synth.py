#!/usr/bin/env python3
"""Run the standalone chord-synth firmware in sim674 with board models: boot, LCD init, panel start-up, then
fader moves and STOP, while checking the audio stream never underruns. Writes synth_sim.wav and synth_lcd.png.
Usage: python3 test_synth.py [seconds of audio] [outdir]"""
import os, sys, time, math, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board
SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 1.4
OUTDIR = sys.argv[2] if len(sys.argv) > 2 else os.environ.get('TMPDIR', '/tmp')
K = 8                                                  # simulated time runs 8x compressed (synth uses ~1% CPU)
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

def boot(clock):
    sim = S.Sim()
    base, img, syms, top = S.load_elf(sim, os.path.join(HERE, '..', 'cbuild', 'synth', 'out', 'synth.out'))
    B = r8board.Board(sim, clock=clock, time_scale=K)
    sim.mocks[syms['delay_us']] = lambda s: None      # fixed delays (LCD init, oscillator settle): skipped
    return sim, syms, B

ONLY_OFF = os.environ.get('ONLY_OFF')
if ONLY_OFF: B = None
# ---- 1. no master clock: must not hang, must say NOCLK, panel and LCD keep working
sim, syms, B = boot(False)
B.send(0.30, [0xC3, 127]); B.send(0.31, [0xCB, 100])
class Done(Exception): pass
def run_until(sim, B, secs):
    stop = int(secs * B.cpu_hz)
    def tr(sim, pc, pk):
        if sim.cycle > stop: raise Done
    sim.trace = tr
    for r in range(64): sim.R[r] = 0
    sim.R[S.ridx(1, 3)] = 0xcafe0000
    try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 12)
    except Done: pass
run_until(sim, B, 1.6)
def lcd_text(B, band, col, n):                         # read characters back from the LCD model
    import re
    src = open(os.path.join(HERE, '..', 'cbuild', 'synth', 'lcd.c')).read()
    font = {m.group(1): bytes(int(x, 16) for x in m.group(2).split(','))
            for m in re.finditer(r"\{'(.)',\{([^}]*)\}\}", src)}
    inv = {v: k for k, v in font.items()}
    return ''.join(inv.get(bytes(B.lcd[band * 128 + col + 6 * i: band * 128 + col + 6 * i + 5]), '?') for i in range(n))
print(f'  no clock: LCD band 0 reads "{lcd_text(B, 0, 2, 8)}" "{lcd_text(B, 0, 58, 5)}"; {len(B.samples)} slots')
check('no clock: firmware keeps running and shows NOCLK', lcd_text(B, 0, 58, 5) == 'NOCLK' and lcd_text(B, 0, 2, 8) == 'R8 CHORD')
check('no clock: fader still moves its bar', B.lcd[7 * 128 + 12] == 0xFF and B.lcd[1 * 128 + 12] != 0)

# ---- 2. with the clock
sim, syms, B = boot(True)

# script of panel input (seconds after boot)
T0 = 0.25                                              # after start-up
moves = [(T0 + 0.00, [0xCB, 100]),                     # MASTER up
         (T0 + 0.01, [0xC3, 127]), (T0 + 0.02, [0xC5, 90]), (T0 + 0.03, [0xC7, 110]), (T0 + 0.05, [0xCA, 60]),
         (T0 + 0.20, [0xA0, 0x03]), (T0 + 0.21, [0xA0, 0x04]),     # STOP: mute
         (T0 + 0.30, [0xA0, 0x03]), (T0 + 0.31, [0xA0, 0x04])]     # STOP again: unmute
for t, d in moves: B.send(t, d)

t = time.time()
run_until(sim, B, T0 + SECS)
dt = time.time() - t
print(f'simulated {sim.cycle / B.cpu_hz:.2f} s in {dt:.0f} s ({sim.ninstr:,} instructions)')
print(f'  LCD band 0 reads "{lcd_text(B, 0, 2, 8)}" "{lcd_text(B, 0, 58, 5)}"')
check('measured sample rate shown: 44100', lcd_text(B, 0, 58, 5) == '44100')
check('both serializers get the same samples', B.xbuf4 == [v for c, v in B.samples][:len(B.xbuf4)] and len(B.xbuf4) >= len(B.samples) - 1)
regs = B.mcasp_regs
check('McASP set up as stock (XFMT with CPU-bus writes, clocks, TDM, serializers 2/4, pins)',
      (regs.get(0xA8), regs.get(0xAC), regs.get(0xB0), regs.get(0xB4), regs.get(0xB8), regs.get(0x188), regs.get(0x190), regs.get(0x10), regs.get(0x14))
      == (0x180F8, 0x113, 0xA3, 0x4000, 3, 0xD, 0xD, 0xFFE9, 0x14000014))
check('GPIO restored to the stock running state', all(B.gpio.get(o + 4) == out and B.gpio.get(o) == d for o, d, out in
      [(0x10, 0x07BCFC80, 0x00400000), (0x38, 0x2400, 0x4000), (0x60, 0, 0), (0x88, 0x001E4C3F, 0xE1C10000), (0xB0, 0xFFFFFCE1, 0x300)]))

start = bytes([0x84,1,1,1,0xF7, 0xA0,1,1,2,0xF7, 0x9E,1,0,0xF7, 0x81,1,0,0xF7, 0x83,1,0,0xF7, 0x8A,1,1,2,0xF7, 0x8B,1,1,0,0xF7])
check('panel start-up bytes sent as captured from stock', bytes(B.tx) == start)
check('LCD init sequence sent', B.lcd_cmds[:14] == [0xAE,0xA2,0xA0,0xC0,0x26,0x81,0x0D,0xA6,0xA4,0x40,0xB0,0x10,0x00,0x2C])
n = len(B.samples)
check(f'audio stream running ({n} slots)', n > B.fs)
check(f'no underruns after start ({B.late} late slots)', B.late == 0)
if B.late_at: print('  first late slots (index, cycle, due):', B.late_at[:5], ' start', B.mcasp_start, ' cpu_hz', B.cpu_hz)
B.wav(os.path.join(OUTDIR, 'synth_sim.wav')); B.png(os.path.join(OUTDIR, 'synth_lcd.png'))

def level(t_from, t_to):          # RMS of the left channel between two times (s after boot), 24-bit full scale = 1
    c0, c1 = t_from * B.cpu_hz, t_to * B.cpu_hz
    xs = [((v >> 8) ^ 0x800000) - 0x800000 for c, v in B.samples[0::2] if c0 <= c < c1]
    return math.sqrt(sum(x * x for x in xs) / max(1, len(xs))) / 8388608
quiet, loud, muted, back = level(T0 - 0.05, T0), level(T0 + 0.12, T0 + 0.19), level(T0 + 0.25, T0 + 0.29), level(T0 + 0.40, T0 + 0.48)
print(f'  RMS: before faders {quiet:.4f}, chord {loud:.4f}, muted {muted:.4f}, unmuted {back:.4f}')
check('silent before any fader moves', quiet < 1e-4)
check('chord audible after faders move', loud > 0.02)
check('STOP mutes', muted < 1e-3)
check('second STOP restores the level', abs(back - loud) < 0.1 * loud)
peak = max(abs(((v >> 8) ^ 0x800000) - 0x800000) for c, v in B.samples) / 8388608
check(f'no clipping (peak {peak:.3f} of full scale)', peak < 0.99)
# ---- 3. power switch off: fade out, OFF on the screen, GP7[0] (power hold) driven low
sim, syms, B = boot(True)
B.send(0.25, [0xCB, 100]); B.send(0.26, [0xC3, 127])
class Sw(Exception): pass
T_OFF = 0.45
def tr2(sim, pc, pk):
    if B.ext[0xB0] and sim.cycle > T_OFF * B.cpu_hz: B.ext[0xB0] = 0; B.off_cycle = sim.cycle
    if sim.cycle > 0.65 * B.cpu_hz: raise Sw
sim.trace = tr2
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 12)
except Sw: pass
held = B.pin(0x88, 16)
cut = [c for c, v in B.samples]
print(f'  switch off at {T_OFF}s; LCD band 3 reads "{lcd_text(B, 3, 55, 3)}"; GP7[0] = {held}')
check('switch off: power hold GP7[0] driven low', held == 0)
check('switch off: screen shows OFF', lcd_text(B, 3, 55, 3) == 'OFF')
before = [v for c, v in B.samples if c is not None and B.off_cycle - 0.05 * B.cpu_hz < c < B.off_cycle]
tail = [v for c, v in B.samples if c is not None and c > B.off_cycle][-200:]
sx = lambda v: (v ^ 0x80000000) - 0x80000000
print(f'  peak before the switch {max(abs(sx(v)) for v in before) / 2**31:.4f}, last 200 slots {max(abs(sx(v)) for v in tail) / 2**31:.6f} of full scale')
check('switch off: audio faded (>= 26 dB down) before the cut', max(abs(sx(v)) for v in before) > 1 << 24 and max(abs(sx(v)) for v in tail) * 20 < max(abs(sx(v)) for v in before))
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
