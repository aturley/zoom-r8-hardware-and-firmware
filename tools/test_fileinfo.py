#!/usr/bin/env python3
"""FILEINFO.BIN (cbuild/utils: fileinfo.c + browse.c) in sim674 with the stock functions mocked (r8mock.py).
The menu engine is a scripted stand-in: each time a menu opens it records the title and labels, then 'picks' items
by storing the index in the descriptor's first state word and calling the item's handler (as the real engine does,
0xC078F3F8), then EXITs. Checks the folder listing (folders first, sorted, '.' names hidden), going into folders,
the info lines for plain files and WAVs (16-bit, 24-bit EXTENSIBLE, float), and an empty folder."""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock, mkutil
W = os.path.join(HERE, '..')
UO = lambda n: os.path.join(W, 'cbuild', 'utils', 'out', n)
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

def wav(rate, bits, ch, frames, fmt=1, ext=False):
    ba = ch * bits // 8
    if ext:
        f = struct.pack('<HHIIHHHHIH14s', 0xFFFE, ch, rate, rate * ba, ba, bits, 22, bits, 0, fmt, b'\0' * 14)
    else:
        f = struct.pack('<HHIIHH', fmt, ch, rate, rate * ba, ba, bits)
    data = bytes(frames * ba)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', len(f)) + f + b'LIST' + struct.pack('<I', 5) + b'abcde\0' + b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(body)) + body

img, base, entry, top = mkutil.pack(UO('fileinfo.out'))
files = {
    'A:\\NOTES.TXT': b'hello world',
    'A:\\._NOTES.TXT': b'junk',
    'A:\\TRASH~16\\x': b'',
    'A:\\.Trashes\\x': b'',
    'A:\\ZOOM_R8\\PROJ000\\TRACK01.WAV': wav(44100, 16, 2, 66150),          # 1.50 s
    'A:\\ZOOM_R8\\PROJ000\\MASTER.WAV': wav(48000, 24, 1, 48000 * 75 + 24000, ext=True),   # 1:15.50
    'A:\\ZOOM_R8\\LOOP\\TALKING.WAV': wav(44100, 32, 1, 441, fmt=3),
    'A:\\ZOOM_R8\\SYS.BIN': bytes(1234),
    'A:\\AAA\\B\\C\\D.TXT': b'',
    'A:\\LONGNA~1.TXT': bytes(2000),
    'A:\\_NOTES~1.TXT': b'mac junk',
}
lfns = {'A:\\LONGNA~1.TXT': 'a long name.txt', 'A:\\_NOTES~1.TXT': '._NOTES.TXT', 'A:\\ZOOM_R8\\PROJ000\\TRACK01.WAV': 'Track 01 (take 2).wav'}

def nested(sim, fn, a4=None, stop=None, b4=None):
    R = list(sim.R); pend, br = sim.pend, sim.branches
    sim.pend, sim.branches = [], []
    sim.R[S.ridx(1, 15)] = (R[S.ridx(1, 15)] - 0x800) & ~7
    if a4 is not None: sim.R[4] = a4
    if b4 is not None: sim.R[S.ridx(1, 4)] = b4
    stop = stop or 0xcafe1000 + 16 * len(R) % 0x100
    sim.R[S.ridx(1, 3)] = stop
    sim.run(fn, stop)
    ret = sim.R[4]
    sim.R[:] = R; sim.pend, sim.branches = pend, br
    return ret
SCRATCH = 0xC0080000        # the row drawer's structure for a record's +32 value function (word 3 = the record's +28)

def run(files, plan, mtimes=None, fattrs=None):
    sim = S.Sim()
    sim.write(base, img[32:])
    env = r8mock.R8Env(sim, files)
    env.mtimes = mtimes or {}; env.fattrs = {'A:\\TRASH~16': 2, **(fattrs or {})}; env.lfns = lfns
    seen = []
    rd = lambda a, n: struct.unpack('<%dI' % n, sim.read(a, 4 * n))
    def menu(s):
        desc = s.arg(0)
        d = rd(desc, 12)
        labels, values = [], []
        for r in rd(d[8], d[0]):
            rec = rd(r, 20); labels.append(s.cstr(rec[0]))
            if rec[8]:
                sim.write(SCRATCH, struct.pack('<4I', 0, 0, 0, rec[7]))
                values.append(s.cstr(nested(sim, rec[8], SCRATCH)))
            else: values.append(None)
        seen.append((s.cstr(d[1]), labels, d, values))
        picks = plan.pop(0) if plan else []
        for i in picks:
            sim.write(d[5], struct.pack('<I', i))
            nested(sim, rd(rd(d[8] + 4 * i, 1)[0] + 16, 1)[0])
        s.R[4] = 0
    sim.mocks[r8mock.ADDR['r8_menu_engine']] = menu
    rc = sim.call(entry, (0, 0))
    return rc, seen, env

# root: pick ZOOM_R8/ then NOTES.TXT; in ZOOM_R8: PROJ000/ then SYS.BIN; in PROJ000: both WAVs; info menus: EXIT
rc, seen, env = run(files, [[1, 3], [1, 2], [0, 1], [], [], [], [], [], []])
print('  menus opened:', [(t, list(zip(l, v))) for t, l, d, v in seen])
pairs = lambda m: list(zip(m[1], m[3]))
root = seen[0]
check('root: label FOLDER or size, value the (long) name; folders first, sorted ignoring case; . names, ._ long names, hidden entries not shown',
      root[0] == 'FILE INFO' and pairs(root) == [('FOLDER', 'AAA'), ('FOLDER', 'ZOOM_R8'), ('2K', 'a long name.txt'), ('11B', 'NOTES.TXT')])
rrecs = [struct.unpack('<20I', env.sim.read(r, 80)) for r in struct.unpack('<%dI' % root[2][0], env.sim.read(root[2][8], 4 * root[2][0]))]
check('descriptor in the SD CARD form (state words, 0x10013, SD CARD callback); rows read-only for the wheel (record +24 = 9)',
      root[2][7] == 0x10013 and root[2][9] == 0xC07ADF74 and root[2][2:5] == (0, 0, 0) and all(r[6] == 9 for r in rrecs))
z = seen[1]
check('picking a folder opens it, titled with its name', z[0] == 'ZOOM_R8' and pairs(z) == [('FOLDER', 'LOOP'), ('FOLDER', 'PROJ000'), ('1K', 'SYS.BIN')])
p = seen[2]
check('nested folder; long names sort by the long name', p[0] == 'PROJ000' and p[3] == ['MASTER.WAV', 'Track 01 (take 2).wav'])
mw = len(files['A:\\ZOOM_R8\\PROJ000\\MASTER.WAV'])
check('24-bit EXTENSIBLE mono WAV info', seen[3][0] == 'MASTER.WAV' and pairs(seen[3]) == [('SIZE', str(mw)),
      ('DATE', '2026-11-12'), ('TIME', '07:03:20'), ('RATE', '48000'), ('BITS', '24'), ('CHANNELS', 'MONO'), ('LENGTH', '1:15.50')])
check('16-bit stereo WAV info, titled with the long name (path uses the 8.3 name)', seen[4][0] == 'Track 01 (take 2).wav'
      and pairs(seen[4])[3:] == [('RATE', '44100'), ('BITS', '16'), ('CHANNELS', 'STEREO'), ('LENGTH', '0:01.50')])
check('after EXIT from PROJ000, still in ZOOM_R8: SYS.BIN info (size, date, time only)',
      seen[5][0] == 'SYS.BIN' and pairs(seen[5]) == [('SIZE', '1234'), ('DATE', '2026-11-12'), ('TIME', '07:03:20')])
check('after EXIT from ZOOM_R8, back at the root: NOTES.TXT (no stale long name); then EXIT returns, files closed',
      seen[6][0] == 'NOTES.TXT' and rc == 0 and len(seen) == 7 and not env.fds)
rc, seen, env = run(files, [[3], []], mtimes={'A:\\NOTES.TXT': (2011, 1, 1, 0, 0, 0)}, fattrs={'A:\\NOTES.TXT': 1})
check('read-only flag and the file\'s own date', pairs(seen[1]) == [('SIZE', '11'), ('DATE', '2011-01-01'), ('TIME', '00:00:00'), ('ATTR', 'READ-ONLY')])
rc, seen, env = run({'A:\\ZOOM_R8\\LOOP\\TALKING.WAV': files['A:\\ZOOM_R8\\LOOP\\TALKING.WAV']}, [[0], [0], [0], []])
check('float WAV', pairs(seen[-1])[3:] == [('RATE', '44100'), ('BITS', '32 FLOAT'), ('CHANNELS', 'MONO'), ('LENGTH', '0:00.01')])
rc, seen, env = run({'A:\\._X': b''}, [[0]])
check('a folder with only hidden things shows (EMPTY) with no value; picking it does nothing',
      seen[0][1] == ['(EMPTY)'] and seen[0][3] == [None] and len(seen) == 1 and rc == 0)

# the scroll fix (browse.c value_of_c): the row drawer calls the value function from 0xC078E7C4 for the selected row;
# if the stock scroller is running (flag 0xC0813790) for another item it must be stopped (0xC07EC184) and the flag
# cleared; for the same item, or from the other call site (0xC078E980), left alone
rc, seen, env = run(files, [[]])
sim = env.sim
stops = []
sim.mocks[0xC07EC184] = lambda s: stops.append(1)
recs = struct.unpack('<%dI' % seen[0][2][0], sim.read(seen[0][2][8], 4 * seen[0][2][0]))
def val(i, site, flag):
    rec = struct.unpack('<20I', sim.read(recs[i], 80))
    sim.wr32(0xC0813790, flag); sim.write(SCRATCH, struct.pack('<4I', 0, i, 0, rec[7]))
    r = sim.cstr(nested(sim, rec[8], SCRATCH, stop=site))
    return r, sim.rd32(0xC0813790)
SEL, OTHER = 0xC078E7C4, 0xC078E980
steps = [val(2, SEL, 0), val(2, SEL, 1), val(1, OTHER, 1), val(3, SEL, 1), val(3, SEL, 0)]
print('  scroll steps:', steps, 'stops', len(stops))
check('scroll fix: same item or other rows leave the scroller; a newly selected item stops it and clears the flag',
      [f for _, f in steps] == [0, 1, 1, 0, 0] and len(stops) == 1 and steps[3][0] == 'NOTES.TXT')

# put_size, called directly
import re
sym = {m.group(2): int(m.group(1), 16) for m in re.finditer(r'^\s*([0-9a-f]{8})\s+(\w+)\s*$', open(UO('fileinfo.map')).read(), re.M)}
sim = S.Sim(); sim.write(base, img[32:]); r8mock.R8Env(sim, {})
got = []
for b in (0, 1023, 1024, 2000, 1023 * 1024, 1024 * 1024 * 3 + 300000, 99 * 1024 * 1024, 2 * 1024 ** 3, 0xFFFFFFFF):
    sim.write(SCRATCH, bytes(16)); sim.call(sym['put_size'], (SCRATCH, b)); got.append(sim.cstr(SCRATCH))
check('sizes: ' + ' '.join(got), got == ['0B', '1023B', '1K', '2K', '1.0M', '3.3M', '99M', '2.0G', '4.0G'])
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
