#!/usr/bin/env python3
"""SAVE (looper.c lp_save + fat.c fat_create) in sim674: record 0.1 s of input into track 1, SAVE it, and check the
card the way a computer would. The card holds a fragmented FAT (free clusters scattered), a deleted directory entry
(its slot is reused) and REC00003.WAV (so the next name is REC00004.WAV). Afterwards the simulated card is written
out as a disk image, checked with macOS fsck_msdos (read-only), mounted read-only and the new WAV compared with the
recording. Also: SAVE refuses tracks without a recording, and refuses while USB storage is writable."""
import os, sys, math, time, struct, subprocess, tempfile, wave, re
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
W = os.path.join(HERE, '..')
K = 4
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
def wavfile(rate, frames, f):
    data = b''.join(struct.pack('<h', int(0.3 * 32767 * math.sin(2 * math.pi * f * i / rate))) for i in range(frames))
    fmt = struct.pack('<HHIIHH', 1, 1, rate, rate * 2, 2, 16)
    return b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVEfmt ' + struct.pack('<I', 16) + fmt + b'data' + struct.pack('<I', len(data)) + data
F = fat32img.Fat32()
F.mkdir('ZOOM_R8'); F.mkdir('ZOOM_R8/LOOP')
F.add('ZOOM_R8/LOOP/a-one.wav', wavfile(44100, 22050, 500), fragment=True)
F.deleted('ZOOM_R8/LOOP')
F.add('ZOOM_R8/LOOP/REC00003.WAV', wavfile(44100, 4410, 800), fragment=True)
F.add('ZOOM_R8/LOOP/b-two.wav', wavfile(44100, 22050, 300), fragment=True)
F.add('ZOOM_R8/keep.txt', b'do not touch ' * 100)
blocks, nblk = F.image()
fsi = bytearray(blocks[F.part + 1])                       # a valid FSInfo free count, as a formatted card has
struct.pack_into('<I', fsi, 488, sum(1 for v in F.fat[2:] if v == 0)); blocks[F.part + 1] = bytes(fsi)
before = dict(blocks)
sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'sampler', 'out', 'sampler.out'))
B = r8board.Board(sim, clock=True, time_scale=K)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
U = r8usb.Musb(sim, B)
sim.mocks[syms['delay_us']] = lambda s: None
FS = 44100
def adc(k):
    i, right = k >> 1, k & 1
    x = 0.25 * math.sin(2 * math.pi * 1300 * i / FS) if right else 0.5 * math.sin(2 * math.pi * 700 * i / FS)
    return int(round(x * 8388607)) & 0xFFFFFF
B.rx_signal = adc
B.panel_pos = [95, 0, 95, 0, 95, 0, 95, 0, 100]
res = {}
U.start(r8usb.mac_enumeration(res))
out = {}
def talk(text, wait_for=' ok', cycles=1_500_000_000):
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
    out['norec'] = yield from talk('2 SAVE\r')
    out['rec'] = yield from talk('1 REC\r'); yield from wait_s(0.1)
    out['stop'] = yield from talk('REC-STOP\r')
    sim.wr32(syms['msc_writable'], 1); sim.wr32(syms['msc_present'], 1)   # as after MSC-RW
    out['rw'] = yield from talk('1 SAVE\r')
    sim.wr32(syms['msc_writable'], 0); sim.wr32(syms['msc_present'], 0)
    sim.wr32(syms['msc_in_use'], 1)                       # as after the host's PREVENT MEDIUM REMOVAL
    out['inuse'] = yield from talk('1 SAVE\r')
    sim.wr32(syms['msc_in_use'], 0)
    out['save'] = yield from talk('1 SAVE\r')
    out['offline_after'] = sim.rd32(syms['msc_offline'])
    out['rec2'] = yield from talk('4 REC\r'); yield from wait_s(0.6)
    out['stop2'] = yield from talk('REC-STOP\r')
    out['save2'] = yield from talk('4 SAVE\r')
    out['rescan'] = yield from talk('RESCAN\r')
    out['files'] = yield from talk('LOOPFILES\r')
    out['loops'] = yield from talk('LOOPS\r')
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
print(f'  run: {sim.cycle:,} cycles ({sim.cycle / B.cpu_hz:.2f} simulated s) in {time.time() - t:.0f} s; host errors: {U.errors}; card errors: {C.errors}')
for k in ('looper', 'norec', 'stop', 'rw', 'save', 'save2', 'files', 'loops'): print(f'  {k}: {out.get(k, "")!r}')
fr = int(re.findall(r'(\d+)\s+frames', out.get('stop', ''))[0]) if re.findall(r'(\d+)\s+frames', out.get('stop', '')) else 0
check('SAVE on a track without a recording is refused', 'no recording on that track' in out.get('norec', ''))
check('SAVE refused while USB storage is on and writable', 'writable over USB' in out.get('rw', ''))
check('SAVE refused while the computer has the card mounted', 'eject it there first' in out.get('inuse', ''))
check('SAVE writes REC00004.WAV (after REC00003)', 'saved as REC00004.WAV' in out.get('save', ''))
check('USB storage back online after saving', out.get('offline_after') == 0)
fr2 = int(re.findall(r'(\d+)\s+frames', out.get('stop2', ''))[0]) if re.findall(r'(\d+)\s+frames', out.get('stop2', '')) else 0
check(f'a second, longer recording ({fr2} frames) saves as REC00005.WAV', 'saved as REC00005.WAV' in out.get('save2', '') and fr2 > 20000)
check('the new file is in the looper\'s list and track 1 is named after it', 'REC00004.WAV  <- track 1' in out.get('files', '') and '1 REC00004.WAV' in out.get('loops', ''))
changed = sorted(b for b in set(C.image) | set(before) if C.image.get(b) != before.get(b))
print(f'  {len(changed)} blocks changed; card writes {C.writes[:6]}{"..." if len(C.writes) > 6 else ""}')
check('RESCAN re-reads the folder (5 WAVs) and keeps each track on its file', ' 5 files' in out.get('rescan', '') and 'REC00004.WAV  <- track 1' in out.get('files', ''))
check('no card protocol errors', not C.errors)
# the card as a computer sees it
tmp = tempfile.mkdtemp()
imgp = os.path.join(tmp, 'card.img')
with open(imgp, 'wb') as f:
    f.truncate((nblk + 1024) * 512)
    for b, d in C.image.items(): f.seek(b * 512); f.write(d)
fsck_ok, wav_ok, other_ok, info = False, False, False, ''
try:
    att = subprocess.run(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nomount', imgp], capture_output=True, text=True)
    devs = [l.split()[0] for l in att.stdout.splitlines() if l.strip()]
    part = next((d for d in devs if d.endswith('s1')), None)
    print('  attached:', devs)
    if part:
        fk = subprocess.run(['fsck_msdos', '-n', part], capture_output=True, text=True)
        print('  fsck_msdos -n:', ' | '.join(l for l in (fk.stdout + fk.stderr).splitlines() if l.strip()))
        txt = fk.stdout + fk.stderr
        fsck_ok = fk.returncode == 0 and 'Fix?' not in txt and 'INCORRECT' not in txt.upper()
        mnt = os.path.join(tmp, 'mnt'); os.mkdir(mnt)
        m = subprocess.run(['mount', '-t', 'msdos', '-o', 'rdonly', part, mnt], capture_output=True, text=True)
        if m.returncode == 0:
            try:
                p = os.path.join(mnt, 'ZOOM_R8', 'LOOP', 'REC00004.WAV')
                w = wave.open(p); n = w.getnframes()
                d = struct.unpack(f'<{2 * n}h', w.readframes(n))
                info = f'{w.getnchannels()} ch, {w.getframerate()} Hz, {w.getsampwidth() * 8}-bit, {n} frames'
                L = [v / 32767 for v in d[0::2]]; R = [v / 32767 for v in d[1::2]]
                def tone(x, f):
                    ww = 2 * math.pi * f / FS; cc = 2 * math.cos(ww); s1 = s2 = 0.0
                    for v in x: s1, s2 = v + cc * s1 - s2, s1
                    return 2 * math.sqrt(max(0.0, s1 * s1 + s2 * s2 - cc * s1 * s2)) / len(x)
                info += f'; left 700 Hz {tone(L, 700):.3f}, right 1300 Hz {tone(R, 1300):.3f}'
                wav_ok = w.getnchannels() == 2 and w.getframerate() == 44100 and n == fr and abs(tone(L, 700) - 0.5) < 0.01 and abs(tone(R, 1300) - 0.25) < 0.01
                w2 = wave.open(os.path.join(mnt, 'ZOOM_R8', 'LOOP', 'REC00005.WAV')); n2 = w2.getnframes()
                d2 = struct.unpack(f'<{2 * n2}h', w2.readframes(n2))
                L2 = [v / 32767 for v in d2[0::2]]
                info += f'; REC00005: {n2} frames, left 700 Hz {tone(L2, 700):.3f}'
                wav_ok = wav_ok and n2 == fr2 and abs(tone(L2, 700) - 0.5) < 0.01
                other_ok = open(os.path.join(mnt, 'ZOOM_R8', 'keep.txt'), 'rb').read() == b'do not touch ' * 100 and \
                           os.path.getsize(os.path.join(mnt, 'ZOOM_R8', 'LOOP', 'a-one.wav')) == 44 + 44100
            finally: subprocess.run(['umount', mnt], capture_output=True)
        else: print('  mount failed:', m.stderr.strip())
finally:
    for d in reversed(devs if 'devs' in dir() else []):
        if not d.endswith('s1'): subprocess.run(['hdiutil', 'detach', '-force', d], capture_output=True)
    os.remove(imgp)
check('macOS fsck_msdos finds the volume consistent, with no warnings (FSInfo count kept right)', fsck_ok)
check(f'REC00004.WAV and REC00005.WAV read back as the recordings ({info})', wav_ok)
check('other files untouched', other_ok)
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
