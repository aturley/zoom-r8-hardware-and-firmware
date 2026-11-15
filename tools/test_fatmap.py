#!/usr/bin/env python3
"""fat.c's random-access reading, listing and folders (cbuild/forth/fat.c), called directly in sim674 against the SD
card model: fat_map + fat_read_some / fat_read_at at random offsets in a contiguous and a fragmented file, compared
byte for byte; the max_blocks bound; fat_next paging through 300 long-named entries and resuming from saved
positions; matching 8.3 names; fat_mkdir plus a file inside the new folder, checked with macOS fsck_msdos."""
import os, sys, struct, random, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8sd, fat32img
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

rng = random.Random(5)
SPC = 8                                                    # 4 KB clusters: directories span several clusters
F = fat32img.Fat32(spc=SPC)
big = bytes(rng.getrandbits(8) for _ in range(3 * 1024 * 1024 + 777))
frag = bytes(rng.getrandbits(8) for _ in range(200 * 1024 + 13))
F.add('BIG.BIN', big)
F.add('FRAG.BIN', frag, fragment=True)
F.mkdir('MANY')
names = [f'Long file name number {i:03d}.wav' for i in range(300)]
for nm in names: F.add('MANY/' + nm, nm.encode())
image, nblk = F.image()

sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=image)
sim.mocks[syms['delay_us']] = lambda s: None
STR, ENT, ENT2, MAP, EXT, POS, GOT, DST = 0xC0500000, 0xC0510000, 0xC0510100, 0xC0510200, 0xC0520000, 0xC0510300, 0xC0510304, 0xC0600000
def call(f, *a): return sim.call(syms[f], a) & 0xFFFFFFFF
def cstr(s, at=STR): sim.write(at, s.encode() + b'\0'); return at
def ent(at=ENT):
    b = sim.read(at, 92)
    return dict(clus=struct.unpack_from('<I', b, 0)[0], size=struct.unpack_from('<I', b, 4)[0], attr=b[8],
                name=b[9:73].split(b'\0')[0].decode('latin1'), name83=b[73:86].split(b'\0')[0].decode('latin1'),
                date=struct.unpack_from('<H', b, 86)[0])
def fmap():
    b = sim.read(MAP, 20); return struct.unpack('<5I', b)

check('fat_mount', call('fat_mount') == 0)
check('fat_find BIG.BIN', call('fat_find', cstr('BIG.BIN'), ENT) == 0 and ent()['size'] == len(big))
r = call('fat_map', ENT, MAP, EXT, 64); size, cb, n, mx, ex = fmap()
check(f'fat_map of the contiguous 3 MB file: one extent ({n}), cluster bytes {cb}', r == 0 and n == 1 and cb == 512 * SPC and size == len(big))

def read_at(off, n, dst=DST):
    r = call('fat_read_at', MAP, off, dst, n)
    return r, sim.read(dst, n)
ok = True
for _ in range(40):
    off = rng.randrange(0, len(big) - 1); n = rng.randrange(1, min(200000, len(big) - off) + 1)
    if rng.random() < 0.5: off -= off % 512                # half block-aligned (the fast path), half not
    r, got = read_at(off, n, DST + rng.choice((0, 0, 1, 2, 3)))
    if r or got != big[off:off + n]: ok = False; print('   mismatch at', off, n, r); break
check('fat_read_at: 40 random offsets and lengths (aligned and not, odd destinations) match the file', ok)
r, got = read_at(len(big) - 100, 100)
check('the last 100 bytes', r == 0 and got == big[-100:])
check('reading past the end refused (FAT_TOOBIG)', call('fat_read_at', MAP, len(big) - 10, DST, 11) == 4)

# bounded reads
r0 = C.reads
r = call('fat_read_some', MAP, 4096 * 3, DST, 1 << 20, 16, GOT); got = struct.unpack('<I', sim.read(GOT, 4))[0]
check(f'fat_read_some with max_blocks 16 reads 16 blocks ({C.reads - r0}) = 8192 bytes ({got}) of a 1 MB request', r == 0 and got == 8192 and C.reads - r0 == 16 and sim.read(DST, got) == big[12288:12288 + got])
r0 = C.reads
r = call('fat_read_some', MAP, 1000, DST, 5000, 3, GOT); got = struct.unpack('<I', sim.read(GOT, 4))[0]
check(f'unaligned start with max_blocks 3: at most 3 blocks touched ({C.reads - r0}), {got} bytes, correct', r == 0 and C.reads - r0 <= 3 and 0 < got <= 3 * 512 and sim.read(DST, got) == big[1000:1000 + got])
r = call('fat_read_some', MAP, len(big), DST, 100, 4, GOT); got = struct.unpack('<I', sim.read(GOT, 4))[0]
check('fat_read_some at the end: 0 bytes, no error', r == 0 and got == 0)

# the fragmented file
call('fat_find', cstr('FRAG.BIN'), ENT2)
r = call('fat_map', ENT2, MAP, EXT, 512); size, cb, n, mx, ex = fmap()
check(f'fat_map of the fragmented file: {n} extents', r == 0 and n > 20)
ok = all(read_at(o, m)[1] == frag[o:o + m] for o, m in [(rng.randrange(0, len(frag) - 5000), rng.randrange(1, 5000)) for _ in range(25)])
check('25 random reads in the fragmented file match', ok)
check('the whole fragmented file in one fat_read_at matches', read_at(0, len(frag))[1] == frag)
check('a map with room for 8 extents refuses it (FAT_TOOBIG)', call('fat_map', ENT2, MAP, EXT, 8) == 4)
call('fat_map', ENT, MAP + 64, EXT + 4096, 64)            # two maps at once: BIG in a second map ...
call('fat_map', ENT2, MAP, EXT, 512)                       # ... FRAG back in the first
r1 = call('fat_read_at', MAP + 64, 5000, DST, 3000); a = sim.read(DST, 3000)
r2 = call('fat_read_at', MAP, 5000, DST, 3000); b = sim.read(DST, 3000)
check('two maps open at once read their own files', not r1 and not r2 and a == big[5000:8000] and b == frag[5000:8000])

# listing: paging and resuming
call('fat_find', cstr('MANY'), ENT2)
sim.write(POS, bytes(4)); listing = []
while True:
    p = struct.unpack('<I', sim.read(POS, 4))[0]
    if not call('fat_next', ENT2, POS, ENT): break
    listing.append((p, ent()))
real = [(p, e) for p, e in listing if e['name'] not in ('.', '..')]
check(f'fat_next lists all 300 long names in order ({len(real)})', [e['name'] for _, e in real] == names)
check('"." and ".." are returned, as documented', [e['name'] for _, e in listing[:2]] == ['.', '..'])
ok = True
for p, e in rng.sample(real, 30) + [real[-1], real[0]]:
    sim.write(POS, struct.pack('<I', p))                  # resume from a saved position, back and forth
    if not call('fat_next', ENT2, POS, ENT) or ent()['name'] != e['name']: ok = False; break
check('fat_next resumes from 32 saved positions in random order (paging back and forth)', ok)
e0 = real[123][1]
check(f'name83 is filled ({e0["name83"]!r}) and fat_find matches it as well as the long name',
      e0['name83'] and call('fat_find', cstr('MANY/' + e0['name83']), ENT) == 0 and ent()['name'] == e0['name']
      and call('fat_find', cstr('many/' + e0['name'].upper()), ENT) == 0)

# folders
call('fat_find', cstr(''), ENT2)
check('fat_mkdir CONCRETE in the root', call('fat_mkdir', ENT2, cstr('CONCRETE   ')) == 0)
check('it is found as a folder', call('fat_find', cstr('CONCRETE'), ENT2) == 0 and ent(ENT2)['attr'] == 0x10)
call('fat_find', cstr(''), ENT)
check('making it again: FAT_EXISTS (5)', call('fat_mkdir', ENT, cstr('CONCRETE   ')) == 5)
payload = bytes(range(256)) * 40
sim.write(DST, payload + bytes(512))
check('fat_create a file inside the new folder', call('fat_create', ENT2, cstr('OBJECT01DAT'), DST, len(payload), 0) == 0)
check('found again by path, size right', call('fat_find', cstr('CONCRETE/OBJECT01.DAT'), ENT) == 0 and ent()['size'] == len(payload))
call('fat_map', ENT, MAP, EXT, 8)
check('and read back through a map', read_at(0, len(payload))[1] == payload)
sim.write(POS, bytes(4)); sub = []
while call('fat_next', ENT2, POS, ENT): sub.append(ent()['name'])
check(f'the new folder lists ".", "..", OBJECT01.DAT ({sub})', sub == ['.', '..', 'OBJECT01.DAT'])
check('no card protocol errors', not C.errors)

# the card as macOS sees it
with tempfile.TemporaryDirectory() as td:
    part = os.path.join(td, 'part.img')
    with open(part, 'wb') as f:
        for b in range(F.part, nblk):
            f.write(C.image.get(b, bytes(512)))
    rr = subprocess.run(['fsck_msdos', '-n', part], capture_output=True, text=True)
    print('   ' + (rr.stdout + rr.stderr).strip().replace('\n', '\n   ')[-600:])
    check('fsck_msdos -n finds no errors in the partition after mkdir + create', rr.returncode == 0 and 'NOT CLEAN' not in rr.stdout.upper())
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
