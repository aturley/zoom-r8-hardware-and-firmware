#!/usr/bin/env python3
"""RUN UTIL bootstrap (cbuild/launcher: r8_main(1, ..)) and the first utilities (cbuild/utils) in sim674, with the
stock firmware's functions mocked (r8mock.py). A missing or bad A:\\R8UTILS\\UTILMENU.SYS gives a popup and returns.
A good one is loaded to its address, the caches are made coherent (block write-back-invalidate, then L1P
invalidate) before it runs, it is called with its own path and returns to the menu. Runs HELLO as UTILMENU.SYS,
then the diagnostic (UTILDIAG.SYS) and the real UTILMENU.SYS against a mock R8UTILS folder."""
import os, sys, struct, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock, mkutil
W = os.path.join(HERE, '..')
LAUNCH_OUT = os.path.join(W, 'cbuild', 'launcher', 'out', 'launch.out')
UO = lambda n: os.path.join(W, 'cbuild', 'utils', 'out', n)
NO_FILE, INVALID = 0xc06e77c5, 0xc06e785d
MENU = 'A:\\R8UTILS\\UTILMENU.SYS'
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
hello, hbase, hentry, htop = mkutil.pack(UO('hello.out'))
diag, dbase, dentry, dtop = mkutil.pack(UO('utildiag.out'))
menu, mbase, mentry, mtop = mkutil.pack(UO('utilmenu.out'))
def run(files):
    sim = S.Sim()
    _, _, syms, _ = S.load_elf(sim, LAUNCH_OUT)
    env = r8mock.R8Env(sim, files)
    pops = []
    def popup(s):
        a = s.arg(0); pops.append(a if a in (NO_FILE, INVALID) else s.cstr(a)); env.log.append(('popup-call',)); s.R[4] = 0
    sim.mocks[r8mock.ADDR['r8_popup']] = popup
    for i in range(64): sim.R[i] = 0
    sim.R[S.ridx(1, 15)] = 0xc0f00000
    rc = sim.call(syms['r8_entry'], (1, 0))
    return rc, sim, env, pops
def hdr(img, **kw):
    f = dict(zip(('magic', 'version', 'load', 'len', 'entry', 'crc', 'top', 'rsvd'), struct.unpack_from('<8I', img)))
    f.update(kw); return struct.pack('<8I', *f.values())
for name, files, want in (('no UTILMENU.SYS', {}, NO_FILE),
                          ('corrupt (CRC)', {MENU: hello[:-1] + bytes([hello[-1] ^ 1])}, INVALID),
                          ('an APP.BIN (wrong magic)', {MENU: hdr(hello, magic=0x50413852) + hello[32:]}, INVALID),
                          ('load address outside idle DDR', {MENU: hdr(hello, load=0x11800000, entry=0x11800000, top=0x11801000) + hello[32:]}, INVALID),
                          ('data past the idle area', {MENU: hdr(hello, top=0xC0240004) + hello[32:]}, INVALID)):
    rc, sim, env, pops = run(files)
    check(f'{name}: popup, back to the menu, nothing run', rc == 0 and pops == [want] and not env.fds and not any(e[0] == 'wbinv' for e in env.log))
rc, sim, env, pops = run({MENU: hello})
ops = [e[0] for e in env.log if e[0] in ('r8_close', 'wbinv', 'l1pinv', 'popup-call')]
w = next((e for e in env.log if e[0] == 'wbinv'), None)
got = sim.read(hbase, len(hello) - 32); diff = [i for i in range(len(got)) if got[i] != hello[32 + i]]
check(f'good file: loaded at its address exactly (only the runtime\'s init flag changed after running: {len(diff)} bytes)', len(diff) <= 4 and (not diff or diff[-1] - diff[0] < 4))
check(f'caches made coherent before it runs: write-back-invalidate of the image (all caches, wait), then L1P invalidate ({w})',
      w == ('wbinv', hbase, len(hello) - 32, 0x7FFF, 1) and ops.index('wbinv') < ops.index('l1pinv') < ops.index('popup-call'))
check('it runs (HELLO popup) and returns to the menu (0), file closed', pops == ['HELLO from R8UTILS'] and rc == 0 and not env.fds)
# the diagnostic UTILMENU.SYS: lists the folder, writes UTILDIAG.BIN, popup with the count
rc, sim, env, pops = run({MENU: diag, 'A:\\R8UTILS\\HELLO.BIN': hello, 'A:\\R8UTILS\\notes.txt': b'x'})
d = bytes(env.fs.get('A:\\R8UTILS\\UTILDIAG.BIN', b''))
n = struct.unpack_from('<I', d, 4)[0] if len(d) >= 16 else -1
names = [d[16 + i * 0x200 + 4 + 0x12:16 + i * 0x200 + 4 + 0x12 + 13].split(b'\0')[0].decode() for i in range(max(0, n))] if n > 0 else []
check(f'diagnostic UTILMENU: UTILDIAG.BIN with the raw entries ({names}), end code -10406', d[:4] == b'R8FD' and n == 5 and len(d) == 16 + 5 * 0x200
      and names == ['.', '..', 'UTILMENU.SYS', 'HELLO.BIN', 'notes.txt'] and struct.unpack_from('<i', d, 12)[0] == -10406)
check('and a popup with the count, back to the menu', pops == ['UTILMENU: 5 files'] and rc == 0 and not env.fds)
# the real UTILMENU.SYS: a stock-style menu of the *.BIN files, sorted; picking one runs it and returns to the menu
bad = bytearray(hello); bad[-1] ^= 1
app = mkutil.pack(UO('hello.out'))[0]; app = hdr(app, magic=0x50413852) + app[32:]
folder = {MENU: menu, 'A:\\R8UTILS\\ZAP.BIN': bytes(bad), 'A:\\R8UTILS\\notes.txt': b'x', 'A:\\R8UTILS\\HELLO.BIN': hello,
          'A:\\R8UTILS\\SUB\\IN.BIN': hello, 'A:\\R8UTILS\\APP.BIN': app}
rc, sim, env, pops = run(folder)
m = [e for e in env.log if e[0] == 'menu']
rd = lambda a, n: struct.unpack('<%dI' % n, sim.read(a, 4 * n))
check('UTILMENU opens one menu and returns to SD CARD when it closes', len(m) == 1 and rc == 0 and pops == [] and not env.fds)
desc = rd(m[0][1], 12) if m else (0,) * 12
labels = [sim.cstr(rd(r, 1)[0]) for r in rd(desc[8], desc[0])] if m else []
check(f'menu R8 UTILS lists the .BIN files sorted, not folders or other files: {labels}',
      labels == ['APP', 'HELLO', 'ZAP'] and sim.cstr(desc[1]) == 'R8 UTILS' and rd(desc[8] + 4 * desc[0], 1)[0] == 0)
check('descriptor like SD CARD\'s: own zeroed state words, 0x10013, SD CARD\'s callback',
      desc[2:5] == (0, 0, 0) and rd(desc[5], 1) == (0,) and rd(desc[6], 1) == (0,) and desc[5] != desc[6]
      and desc[7] == 0x10013 and desc[9] == 0xC07ADF74 and desc[5] >= mbase and desc[6] < mtop)
recs = [rd(r, 20) for r in rd(desc[8], desc[0])] if m else []
check('item records like FORMAT\'s (handler at +16, 1 at +24, the pad strings at +48..+60), one handler each',
      all(r[1:4] == (0, 0, 0) and r[6] == 1 and r[12:16] == (0xC06E995D,) * 4 for r in recs) and len({r[4] for r in recs}) == 3)
def pick(i):
    del pops[:]; env.log.clear()
    for k in range(64): sim.R[k] = 0
    sim.R[S.ridx(1, 15)] = 0xc0f00000
    sim.call(recs[i][4], ())
    return list(pops)
check('picking HELLO.BIN runs it (caches made coherent first) and returns to the menu',
      pick(1) == ['HELLO from R8UTILS'] and [e[0] for e in env.log if e[0] in ('wbinv', 'l1pinv')] == ['wbinv', 'l1pinv'] and not env.fds)
check('a corrupt utility (CRC) is refused with INVALID FILE, nothing run', pick(2) == [INVALID] and not any(e[0] == 'wbinv' for e in env.log))
check('an app (R8AP) in the folder is refused too', pick(0) == [INVALID])
check('UTILMENU itself is untouched by running a utility', sim.read(mbase, len(menu) - 32 - 64)[:256] == menu[32:32 + 256])
del folder['A:\\R8UTILS\\ZAP.BIN'], folder['A:\\R8UTILS\\HELLO.BIN'], folder['A:\\R8UTILS\\APP.BIN']
rc, sim, env, pops = run(folder)
check('no utilities: popup NO UTILITIES, no menu', pops == ['NO UTILITIES'] and not any(e[0] == 'menu' for e in env.log) and rc == 0)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
