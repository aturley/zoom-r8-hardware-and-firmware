#!/usr/bin/env python3
"""PADTEST app (cbuild/padtest) in sim674 with the board model and an SD card. Checks: pad hits and releases in
the running list ("P1 ON  v  87", "P1 OFF 0.149s") and the held row; pads by bit (F0 00 04 = pad 3, by running
status; F0 02 00 = pad 6); keys with code, down/up and name (A7 03/04); the wheel; an unknown message in hex;
faders left out of the list; one EXIT only listed and arming; EXIT again saves the log as PAD0002.TXT (after an
existing PAD0001.TXT) and restarts into the stock firmware; the card stays consistent. Usage: python3 test_padtest.py"""
import os, sys, struct, re, time, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8sd, fat32img
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
F = fat32img.Fat32()
F.add('PAD0001.TXT', b'old log'); F.mkdir('R8APPS')
blocks, nblk = F.image()
fsi = bytearray(blocks[F.part + 1]); struct.pack_into('<I', fsi, 488, sum(1 for v in F.fat[2:] if v == 0)); blocks[F.part + 1] = bytes(fsi)
sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'padtest', 'out', 'padtest.out'))
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
    while 'PAD+KEY' not in lcd_rows()[0]: yield
    yield from wait_s(0.25)
    out['s0'] = lcd_rows()
    send([0xF0, 0x00, 0x01, 87]); yield from wait_s(0.15)
    out['s1'] = lcd_rows()
    send([0xF0, 0x00, 0x01, 0]); yield from wait_s(0.15)
    out['s2'] = lcd_rows()
    send([0xF0, 0x00, 0x04, 40, 0x00, 0x04, 0]); yield from wait_s(0.05)   # pad 3 hit, release by running status
    send([0xF0, 0x02, 0x00, 50]); yield from wait_s(0.05)                  # pad 6 hit
    out['s3'] = lcd_rows()
    send([0xF0, 0x02, 0x00, 0]); yield from wait_s(0.03)
    send([0xA7, 3]); yield from wait_s(0.03); send([0xA7, 4]); yield from wait_s(0.03)   # key A7 down, up
    send([0xD0, 0x7F]); yield from wait_s(0.03)                            # wheel back one click
    send([0xF5, 0x12, 0x34]); yield from wait_s(0.03)                      # unknown
    send([0xC3, 10]); yield from wait_s(0.05)                              # fader: not listed
    out['s4'] = lcd_rows()
    send([0x93, 3]); yield from wait_s(0.02); send([0x93, 4]); yield from wait_s(0.05)   # one EXIT: listed, armed
    out['s5'] = lcd_rows()
    send([0x93, 3]); yield from wait_s(3)                                  # EXIT again: save, restart
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
for k in ('s0', 's1', 's2', 's3', 's4', 's5'):
    print('  %s:' % k); [print('    |' + r + '|') for r in out.get(k, [])]
E = [''] * 8
s0, s1, s2, s3, s4, s5 = (out.get(k, E) for k in ('s0', 's1', 's2', 's3', 's4', 's5'))
L = lambda sc: [r.rstrip() for r in sc[2:8]]
check('start: title, nothing held, empty list', 'PAD+KEY TEST' in s0[0] and 'held: none' in s0[1] and not any(L(s0)))
check('pad 1 hit: held, "P1 ON  v  87" at the top', 'held: 1' in s1[1] and L(s1)[0] == 'P1 ON  v  87')
m = re.match(r'P1 OFF (\d)\.(\d\d\d)s', L(s2)[0])
check('pad 1 release: "P1 OFF" with about 0.15 s held (%s), the hit below it' % L(s2)[0], 'held: none' in s2[1] and m and 0.12 < int(m.group(1)) + int(m.group(2)) / 1000 < 0.2 and L(s2)[1] == 'P1 ON  v  87')
check('pad 3 (F0 00 04, running status) and pad 6 (F0 02 00): a running list, newest first',
      L(s3)[:4] == ['P6 ON  v  50', 'P3 OFF 0.000s', 'P3 ON  v  40', L(s2)[0]] and 'held: 6' in s3[1])
check('key A7 down and up, the wheel, an unknown message; the fader not listed',
      L(s4)[:4] == ['? F5 12 34', 'wheel -1', 'A7 up REPEAT/STOP', 'A7 dn REPEAT/STOP'] and L(s4)[4].startswith('P6 OFF 0.0'))
check('one EXIT: listed, armed (title), no restart', L(s5)[:2] == ['93 up EXIT', '93 dn EXIT'] and 'EXIT again' in s5[0])
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
                p = os.path.join(mnt, 'PAD0002.TXT')
                log = open(p).read() if os.path.exists(p) else ''
                old = open(os.path.join(mnt, 'PAD0001.TXT'), 'rb').read()
            finally: subprocess.run(['umount', mnt], capture_output=True)
finally:
    for d in reversed(devs):
        if not d.endswith('s1'): subprocess.run(['hdiutil', 'detach', '-force', d], capture_output=True)
    os.remove(imgp)
print('  PAD0002.TXT:'); [print('    ' + l) for l in log.splitlines()[:40]]
check('macOS fsck_msdos finds the volume consistent', fsck_ok)
check('PAD0002.TXT saved with the pad messages, timestamped; PAD0001.TXT untouched',
      re.search(r'^\s*\d+\.\d{6} F0 00 01 57$', log, re.M) and re.search(r'^\s*\d+\.\d{6} F0 00 01 00$', log, re.M) and ' F5 12 34' in log and ' A7 03' in log and ' A7 04' in log and old == b'old log')
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
