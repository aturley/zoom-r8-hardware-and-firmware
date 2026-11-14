#!/usr/bin/env python3
"""Low-battery shutdown (synth/battery.c) in sim674, through POWERLOG (cbuild/powerlog). Starts on USB power (GP6[1]
high): a low C1 does nothing. Then GP6[1] goes low (batteries): with C1 = 208 (below Alkaline's 213) the battery is
LOW; with C1 = 192 (below 203) for longer than batt_empty_us (set to 50 ms here; 4 s on the R8) it is EMPTY: the
log is saved (PWR0001.TXT, with the battery lines) and the power hold GP7[0] is released. Also checks that C1 is
taken from the start-up snapshot. Usage: python3 test_battery.py"""
import os, sys, struct, re, time, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8sd, fat32img
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
F = fat32img.Fat32()
F.mkdir('R8APPS')
blocks, nblk = F.image()
fsi = bytearray(blocks[F.part + 1]); struct.pack_into('<I', fsi, 488, sum(1 for v in F.fat[2:] if v == 0)); blocks[F.part + 1] = bytes(fsi)
sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'powerlog', 'out', 'powerlog.out'))
B = r8board.Board(sim, clock=False, time_scale=1)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
sim.mocks[syms['delay_us']] = lambda s: None
B.ext[0x88] = B.ext.get(0x88, 0) | 2                         # GP6[1] high: USB power
sim.wr32(syms['batt_empty_us'], 50000)
class Restart(Exception): pass
def restart(s): raise Restart
sim.mocks[syms['r8_restart']] = restart
FONT = [bytes(int(x, 0) for x in m.group(1).split(',')) for m in re.finditer(r'\{([^{}]*)\}', open(os.path.join(W, 'cbuild', 'forth', 'font.h')).read().split('FONT6', 1)[1])]
INV = {g: chr(32 + i) for i, g in enumerate(FONT) if len(g) == 6}
INVI = {bytes(x ^ 0xFF for x in g): c for g, c in INV.items()}
def lcd_rows():
    return [''.join(INV.get(bytes(B.lcd[r * 128 + c: r * 128 + c + 6]), INVI.get(bytes(B.lcd[r * 128 + c: r * 128 + c + 6]), '#'))
                    for c in range(0, 126, 6)) for r in range(8)]
def now_s(): return sim.cycle / B.cpu_hz
def wait_s(sec):
    end = sim.cycle + sec * B.cpu_hz
    while sim.cycle < end: yield
def send(data): B.send(now_s() + 0.0005, data)
out = {}
def hold(): return (B.gpio.get(0x88 + 4, 0) >> 16) & 1           # GP7[0] output: the power hold
def flow():
    while 'POWER LOG' not in lcd_rows()[0]: yield
    yield from wait_s(0.3)
    out['c1_snap'] = sim.rd32(syms['panel_c1']); out['hold0'] = hold()
    send([0xC1, 0x01, 0x50]); yield from wait_s(0.3)                  # 208 on USB: nothing
    out['usb_low'] = (sim.rd32(syms['batt_on_usb']), hold())
    B.ext[0x88] &= ~2; yield from wait_s(0.5)                         # unplug USB: batteries; 208 -> LOW
    out['s_low'] = lcd_rows(); out['bat'] = sim.rd32(syms['batt_on_usb'])
    send([0xC1, 0x01, 0x40])                                          # 192: below 203
    t0 = now_s()
    while hold() and now_s() - t0 < 3: yield
    out['off_after'] = now_s() - t0; out['hold1'] = hold(); out['s_off'] = lcd_rows()
G = flow()
class Stop(Exception): pass
n = [0]
def tr(s, pc, pk):
    n[0] += 1
    if n[0] & 31 == 0:
        try: next(G)
        except StopIteration: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
t = time.time(); restarted = False
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 12)
except Stop: pass
except Restart: restarted = True
print(f'  run: {sim.cycle / B.cpu_hz:.2f} simulated s in {time.time() - t:.0f} s')
for k in ('s_low', 's_off'):
    print('  %s:' % k); [print('    |' + r + '|') for r in out.get(k, [])]
check('C1 taken from the start-up snapshot (248)', out.get('c1_snap') == 248)
check('on USB, a low C1 changes nothing (still on USB, power held)', out.get('usb_low') == (1, 1) and out.get('hold0') == 1)
check('on batteries with C1 = 208: LOW listed', out.get('bat') == 0 and any('batt LOW BAT' in r for r in out.get('s_low', [])))
check('C1 = 192: EMPTY, log saved, power hold released (%.2f s)' % out.get('off_after', 0), out.get('hold1') == 0 and out.get('off_after', 9) < 2.5)
check('no card protocol errors', not C.errors)
tmp = tempfile.mkdtemp(); imgp = os.path.join(tmp, 'card.img')
with open(imgp, 'wb') as f:
    f.truncate((nblk + 1024) * 512)
    for b, d in C.image.items(): f.seek(b * 512); f.write(d)
fsck_ok, log, old, devs = False, '', b'', []
try:
    att = subprocess.run(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nomount', imgp], capture_output=True, text=True)
    devs = [l.split()[0] for l in att.stdout.splitlines() if l.strip()]
    part = next((d for d in devs if d.endswith('s1')), None)
    if part:
        fk = subprocess.run(['fsck_msdos', '-n', part], capture_output=True, text=True); txt = fk.stdout + fk.stderr
        fsck_ok = fk.returncode == 0 and 'Fix?' not in txt and 'INCORRECT' not in txt.upper()
        mnt = os.path.join(tmp, 'mnt'); os.mkdir(mnt)
        if subprocess.run(['mount', '-t', 'msdos', '-o', 'rdonly', part, mnt], capture_output=True).returncode == 0:
            try:
                p = os.path.join(mnt, 'PWR0001.TXT')
                log = open(p).read() if os.path.exists(p) else ''
            finally: subprocess.run(['umount', mnt], capture_output=True)
finally:
    for d in reversed(devs):
        if not d.endswith('s1'): subprocess.run(['hdiutil', 'detach', '-force', d], capture_output=True)
    os.remove(imgp)
print('  PWR0001.TXT:'); [print('    ' + l) for l in log.splitlines()[:40]]
check('macOS fsck_msdos finds the volume consistent', fsck_ok)
check('PWR0001.TXT has the battery lines: OK on USB, LOW on batteries, EMPTY',
      'battery OK, on USB, setting Alkaline, C1 248' in log and 'battery LOW, on batteries, setting Alkaline, C1 208' in log
      and 'battery EMPTY, on batteries, setting Alkaline, C1 192' in log)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
