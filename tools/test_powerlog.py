#!/usr/bin/env python3
"""POWERLOG app (cbuild/powerlog) in sim674 with the board model and an SD card. Checks: C1 and C2 shown (hex and
14-bit value); a key under the screen writes MARK 1; a GPIO input change (GP8[10]) is listed and logged; a bit that
toggles fast is reported once as noisy and then ignored; EXIT twice saves PWR0001.TXT with C1, the mark, the GPIO
change and a snapshot, and restarts into the stock firmware. Usage: python3 test_powerlog.py"""
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
def flow():
    while 'POWER LOG' not in lcd_rows()[0]: yield
    yield from wait_s(0.25)
    out['s0'] = lcd_rows()
    send([0xC1, 0x2A, 0x3B]); yield from wait_s(0.02); send([0xC2, 0x00, 0x65]); yield from wait_s(0.05)
    send([0xA3, 3]); yield from wait_s(0.02); send([0xA3, 4]); yield from wait_s(0.05)     # MARK 1
    B.ext[0xB0] ^= 1 << 10; yield from wait_s(0.05)                                         # GP8[10] changes
    for k in range(14): B.ext[0xB0] ^= 1 << 11; yield from wait_s(0.01)                     # GP8[11] toggles fast
    yield from wait_s(0.05)
    out['s1'] = lcd_rows()
    send([0x93, 3]); yield from wait_s(0.02); send([0x93, 4]); yield from wait_s(0.05)
    send([0x93, 3]); yield from wait_s(3)
    out['no_restart'] = True
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
for k in ('s0', 's1'):
    print('  %s:' % k); [print('    |' + r + '|') for r in out.get(k, [])]
E = [''] * 8
s0, s1 = out.get('s0', E), out.get('s1', E)
L = [r.rstrip() for r in s1[3:8]]
check('start: title, C1/C2 not seen yet', 'POWER LOG' in s0[0] and s0[1].startswith('C1 -'))
check('C1 and C2 shown with their 14-bit values', s1[1].startswith('C1 2A 3B =  5435') and s1[2].startswith('C2 00 65 =   101'))
check('GP8[11] changes, then reported once as noisy (list: %s)' % L,
      L[0] == 'GP8[11] noisy' and sum('GP8[11] -> ' in x for x in L) == 4)
check('EXIT again restarts into the stock firmware', restarted and not out.get('no_restart'))
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
check('PWR0001.TXT holds C1, the mark, the GPIO changes, the noisy note and snapshots (GP8[12] set in the bank 8 one); output pins (LCD GP2[14]) not logged',
      re.search(r'^\s*\d+\.\d{6} C1 2A 3B$', log, re.M) and 'MARK 1' in log and 'GP8[10] -> 1' in log and 'GP8[11] noisy' in log
      and log.count('snapshot GPIO') >= 5 and 'GP2[14]' not in log
      and all(int(m, 16) & 0x1000 for m in re.findall(r'snapshot GPIO 8 IN ([0-9A-F]{8})', log)))   # GP8[12] = power switch on
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
