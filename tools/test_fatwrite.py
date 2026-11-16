#!/usr/bin/env python3
"""The streaming writer and split writes (cbuild/forth/fat.c fat_wopen/fat_wwrite/fat_wpatch/fat_wclose/fat_wabort,
sd.c sd_write_start/sd_busy), called directly in sim674 against the SD card model. A 5 MB recording is written in
32 KB chunks into free space full of holes, retrying on FAT_AGAIN while the card programs; meanwhile another file is
saved with fat_create and another is read; then an odd-sized last piece, a WAV-header patch and the step-by-step
close. Everything is read back byte for byte, the refusals are checked, an abort must leave no trace, and macOS
fsck_msdos must find the card clean."""
import os, sys, struct, random, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8sd, fat32img
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
FAT_OK, FAT_SD, FAT_NOFS, FAT_NOTFOUND, FAT_TOOBIG, FAT_EXISTS, FAT_AGAIN, FAT_STATE = range(8)
SD_EBUSY = 8

rng = random.Random(9)
SPC = 8
F = fat32img.Fat32(spc=SPC)
holes = bytes(rng.getrandbits(8) for _ in range(600 * 1024))
F.add('HOLES.BIN', holes, fragment=True)            # every other cluster: the free space after it is full of holes
other = bytes(rng.getrandbits(8) for _ in range(150 * 1024 + 77))
F.add('OTHER.BIN', other)
F.mkdir('REC')
image, nblk = F.image()

sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=image)
sim.mocks[syms['delay_us']] = lambda s: None
STR, DIR, ENT, WR, WR2, EXT, EXT2, MAP, MEXT, TAKEN = (0xC0500000, 0xC0510000, 0xC0510100, 0xC0511000, 0xC0512000,
                                                        0xC0520000, 0xC0528000, 0xC0530000, 0xC0531000, 0xC0510200)
SRC, OBJ, RD = 0xC0600000, 0xC1200000, 0xC1400000
def call(f, *a): return sim.call(syms[f], a) & 0xFFFFFFFF
def cstr(s, at=STR): sim.write(at, s.encode() + b'\0'); return at
def u32(a): return struct.unpack('<I', sim.read(a, 4))[0]

check('mount, find REC', call('fat_mount') == 0 and call('fat_find', cstr('REC'), DIR) == 0)
rec = bytes(rng.getrandbits(8) for _ in range(5 * 1024 * 1024)) + bytes(rng.getrandbits(8) for _ in range(301))
sim.write(SRC, rec)
check('fat_wopen REC/PRINT001.WAV', call('fat_wopen', WR, DIR, cstr('PRINT001WAV'), EXT, 512) == FAT_OK)
check('a second writer while one is open: FAT_STATE', call('fat_wopen', WR2, DIR, cstr('PRINT002WAV'), EXT2, 64) == FAT_STATE)
check('fat_create with the open writer\'s name: FAT_EXISTS', call('fat_create', DIR, cstr('PRINT001WAV'), OBJ, 100, 0) == FAT_EXISTS)

off, again, calls, maxtake, created, readback = 0, 0, 0, 0, None, None
busy_seen = []
while off < len(rec):
    n = min(32768, len(rec) - off)
    if n >= 512: n -= n % 512
    r = call('fat_wwrite', WR, SRC + off, n, TAKEN); t = u32(TAKEN); calls += 1
    if r == FAT_AGAIN:
        again += 1; busy_seen.append(call('sd_busy')); continue
    if r != FAT_OK: print('   fat_wwrite', r, 'at', off); break
    off += t; maxtake = max(maxtake, t)
    if created is None and off > 2 * 1024 * 1024:     # mid-recording: save another file, read another
        objdata = bytes(rng.getrandbits(8) for _ in range(70000)); sim.write(OBJ, objdata + bytes(512))
        created = (call('fat_create', DIR, cstr('OBJECT01DAT'), OBJ, len(objdata), 0), objdata)
        call('fat_find', cstr('OTHER.BIN'), ENT); call('fat_map', ENT, MAP, MEXT, 16)
        readback = call('fat_read_at', MAP, 1234, RD, 60000) == 0 and sim.read(RD, 60000) == other[1234:61234]
check(f'the recording went in: {off} bytes in {calls} calls, {again} FAT_AGAIN retries, chunks of at most {maxtake}',
      off == len(rec) and again > 0 and maxtake <= 128 * 512)
check(f'while a chunk programs, sd_busy reports SD_EBUSY ({busy_seen.count(SD_EBUSY)} of {len(busy_seen)} polls)', SD_EBUSY in busy_seen)
check('meanwhile fat_create saved another file in the same folder', created and created[0] == FAT_OK)
check('meanwhile another file read back correctly', readback)
n_ext = u32(WR + 24)                               # fat_wr_t: size, state, ck, scan, scanned, ext, n (+24)
check(f'the writer\'s clusters came in many runs ({n_ext}), from the holes', n_ext > 20)
check('after the odd-sized end: no more writes (FAT_STATE)', call('fat_wwrite', WR, SRC, 1024, TAKEN) == FAT_STATE)
hdr = b'RIFF' + struct.pack('<I', len(rec) - 8) + b'WAVEfmt ' + bytes(range(28))
sim.write(STR + 0x100, hdr)
check('fat_wpatch the 44-byte header at 0', call('fat_wpatch', WR, 0, STR + 0x100, len(hdr)) == FAT_OK)
check('fat_wpatch across a block boundary refused', call('fat_wpatch', WR, 500, STR + 0x100, 20) == FAT_TOOBIG)
check('before close the recording is not visible', call('fat_find', cstr('REC/PRINT001.WAV'), ENT) == FAT_NOTFOUND)
steps = 0
while True:
    r = call('fat_wclose', WR); steps += 1
    if r != FAT_AGAIN: break
check(f'fat_wclose finished in {steps} bounded calls', r == FAT_OK and steps > 2)
expect = hdr + rec[len(hdr):]
check('the recording is found, size right', call('fat_find', cstr('REC/PRINT001.WAV'), ENT) == 0 and u32(ENT + 4) == len(rec))
call('fat_map', ENT, MAP, MEXT, 512)
check(f'read back through a map ({u32(MAP + 8)} extents): byte for byte, header patched',
      call('fat_read_at', MAP, 0, RD, len(rec)) == 0 and sim.read(RD, len(rec)) == expect)
call('fat_find', cstr('REC/OBJECT01.DAT'), ENT); call('fat_map', ENT, MAP, MEXT, 64)
check('the file saved mid-recording reads back too', call('fat_read_at', MAP, 0, RD, len(created[1])) == 0 and sim.read(RD, len(created[1])) == created[1])

# abort
check('another writer can open now', call('fat_wopen', WR2, DIR, cstr('PRINT002WAV'), EXT2, 64) == FAT_OK)
call('fat_wwrite', WR2, SRC, 65536, TAKEN); call('sd_busy'); call('fat_wwrite', WR2, SRC, 65536, TAKEN)
call('fat_wabort', WR2)
check('after fat_wabort the file does not exist', call('fat_find', cstr('REC/PRINT002.WAV'), ENT) == FAT_NOTFOUND)
check('and a new writer can open (none active)', call('fat_wopen', WR2, DIR, cstr('PRINT003WAV'), EXT2, 64) == FAT_OK)
call('fat_wabort', WR2)
check('no card protocol errors', not C.errors)

with tempfile.TemporaryDirectory() as td:
    part = os.path.join(td, 'part.img')
    with open(part, 'wb') as f:
        for b in range(F.part, nblk): f.write(C.image.get(b, bytes(512)))
    rr = subprocess.run(['fsck_msdos', '-n', part], capture_output=True, text=True)
    out = (rr.stdout + rr.stderr).strip()
    print('   ' + out.replace('\n', '\n   ')[-700:])
    check('fsck_msdos -n: no errors, no lost clusters', rr.returncode == 0 and 'NOT CLEAN' not in out.upper() and 'lost' not in out.lower())
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
