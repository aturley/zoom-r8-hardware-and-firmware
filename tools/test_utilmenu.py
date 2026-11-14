#!/usr/bin/env python3
"""TRACK EDIT hook + handlers of utilmenu.py (REVERSE + RUN APP + RUN UTIL) in sim2, on the real menu snapshot (MENUDUMP.BIN).
The flash code itself is tested elsewhere (test_revmenu.py for REVERSE, test_launcher.py for the launcher): here the
handlers must load the whole flash blob once and hand control to the right entry, as a call."""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import utilmenu as UM, appmenu as AM, menudump as MD, revmenu as R, sim2, celf
W = os.path.join(HERE, '..')
LOUT = os.path.join(W, 'cbuild', 'launcher', 'out', 'launch.out')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
stock = open(os.path.join(W, 'fw', 'R8SYS.BIN'), 'rb').read()
img, _, _, bl, _ = UM.patch(stock, LOUT)
flash = bytearray(b'\xff' * 0x800000); flash[0x80000:0x80000 + len(img) - 0x40] = img[0x40:]
cave, a, caddr = UM.build_cave(bl)
blob = AM.build_blob(LOUT)
snap = open(os.path.join(W, 'data', 'MENUDUMP.BIN'), 'rb').read()
DESC = 0xc05e4fc0
CSR = ('A10', 'A11', 'A12', 'A13', 'A14', 'A15', 'B10', 'B11', 'B12', 'B13')

def mk(clob):
    s = sim2.Sim(cave, MD.CAVE, {}); s.clobber = clob; s.far_mocks = True
    for i, b in enumerate(snap[32:32 + 0x20000]): s.mem[0xc05e0000 + i] = b
    for i, b in enumerate(snap[32 + 0x20000:]): s.mem[0xc0812000 + i] = b
    L = []
    def m_flash(sm):
        ad, buf, n = sm.R['A4'], sm.R['B4'], sm.R['A6']
        for i, b in enumerate(flash[ad:ad + n]): sm.mem[buf + i] = b      # loaded, but not run here
        L.append(('flash', ad, buf, n)); sm.R['A4'] = 0
    def rec(name, ret=0):
        def f(sm): L.append((name, sm.R['A4'], sm.R['B4'])); sm.R['A4'] = ret
        return f
    s.mocks = {MD.FLASH_READ: m_flash, MD.CACHE_WBINV: rec('wbinv'), MD.CACHE_INV_L1P: rec('l1pinv'), R.CARD_CHECK: rec('card_check')}
    return s, L

def run(s, entry, args, ret):
    s.R.update(args); s.R['B3'] = ret; s.R['B15'] = 0xc0900000
    for r in CSR: s.R[r] = 0x1000 + int(r[1:])
    s.pending = []; s.branch = None
    end = s.run(a.labels[entry], max_cycles=s.cycle + 5_000_000)
    return end, s.R['B15'] == 0xc0900000 and all(s.R[r] == 0x1000 + int(r[1:]) for r in CSR)

for clob in (0xBADBAD00, 0, 0xFFFFFFFF):
    print('--- clobber', hex(clob))
    s, L = mk(clob)
    w = s.rd32
    lst = w(DESC + 32); r1 = w(lst + 4)
    end, ok = run(s, 'menu_hook', dict(A4=DESC, B4=0, A6=0, B6=0), 0xc072fec4)
    check('menu_hook continues into the engine with A4 = descriptor, B3 intact', end == MD.MENU_ENGINE and s.R['A4'] == DESC and s.R['B3'] == 0xc072fec4 and ok)
    r2 = w(lst + 8)
    check('TRACK EDIT: 3 items, item 3 = REVERSE (TRIM layout, type 0), slot 3 still empty', w(DESC) == 3 and r2 == r1 + 80 and
          s.rdbytes(w(r2), 8) == b'REVERSE\0' and w(r2 + 16) == a.labels['rev_item'] and w(r2 + 4) == 0 and
          s.rdbytes(r2 + 20, 60) == s.rdbytes(r1 + 20, 60) and w(lst + 12) == 0 and s.rdbytes(r1 + 160, 80) == bytes(80))
    check('the next menu block (list[4], record 0xC05E52B0) untouched', w(lst + 16) == 0xc05e52b0 and s.rdbytes(0xc05e52b0, 80) == snap[32 + 0x52b0:32 + 0x52b0 + 80])
    s.wr32(DESC, 2); end, ok = run(s, 'menu_hook', dict(A4=DESC, B4=0, A6=0, B6=0), 0xc072fec4)
    check('menu rebuilt (count 2 again, our slot still there): REVERSE added again', w(DESC) == 3 and end == MD.MENU_ENGINE)
    s.wr32(DESC, 2); s.wr32(r1 + 80 + 16, 0x12345678); end, ok = run(s, 'menu_hook', dict(A4=DESC, B4=0, A6=0, B6=0), 0xc072fec4)
    check('slot 2 holds something else: menu left alone', w(DESC) == 2 and end == MD.MENU_ENGINE)
    s.wr32(r1 + 80 + 16, a.labels['rev_item'])
    # handlers: run the cave code; it leaves the cave when it calls into the flash code (CODEBUF / LAUNCH)
    end, ok = run(s, 'app_item', dict(A4=0, B4=0), 0xc0780000)
    check('RUN APP: card check, load the whole blob once, cache maintenance, then call the launcher with B3 = return',
          end == AM.LAUNCH and [e[0] for e in L] == ['card_check', 'flash', 'wbinv', 'l1pinv'] and L[1][1:] == (MD.BLOB_FLASH, MD.CODEBUF, len(blob))
          and L[2][1:] == (MD.CODEBUF, len(blob)) and s.R['B3'] == a.labels['app_item_back'] and s.R['A4'] == 0)
    check('blob in RAM: REVERSE code at 0xC0070000, launcher image at 0xC0072000',
          s.rdbytes(MD.CODEBUF, len(blob)) == blob and blob[0x2000:] == celf.image(LOUT)[1] and blob[:len(R.build_blob()[0])] == R.build_blob()[0])
    s.R['A4'] = 7; s.branch = None; end2 = s.run(a.labels['app_item_back'])          # launcher returned (popup case)
    check('launcher returns: RUN APP returns 0 to the engine, callee-saved intact', end2 == 0xc0780000 and s.R['A4'] == 0 and
          s.R['B15'] == 0xc0900000 and all(s.R[r] == 0x1000 + int(r[1:]) for r in CSR))
    n = len(L); end, ok = run(s, 'rev_item', dict(A4=0, B4=0), 0xc0780000)
    check('REVERSE afterwards: no second load, calls 0xC0070000', end == MD.CODEBUF and [e[0] for e in L[n:]] == ['card_check'])
    n = len(L); end, ok = run(s, 'util_item', dict(A4=0, B4=0), 0xc0780000)
    check('RUN UTIL: card check, no second load, calls the launcher with A4 = 1 (r8_main(1, ..): UTILMENU.SYS)',
          end == AM.LAUNCH and [e[0] for e in L[n:]] == ['card_check'] and s.R['A4'] == 1 and s.R['B3'] == a.labels['util_item_back'])
    s.R['A4'] = 3; s.branch = None; end2 = s.run(a.labels['util_item_back'])
    check('launcher returns: RUN UTIL returns 0 to the engine, callee-saved intact', end2 == 0xc0780000 and s.R['A4'] == 0 and
          s.R['B15'] == 0xc0900000 and all(s.R[r] == 0x1000 + int(r[1:]) for r in CSR))
    s, L = mk(clob); end, ok = run(s, 'util_item', dict(A4=0, B4=0), 0xc0780000)
    check('RUN UTIL first: loads the blob once, then calls the launcher with A4 = 1', end == AM.LAUNCH and [e[0] for e in L] == ['card_check', 'flash', 'wbinv', 'l1pinv'] and s.R['A4'] == 1)
    s, L = mk(clob); s.mocks[R.CARD_CHECK] = lambda sm: sm.R.__setitem__('A4', 5)
    end, ok = run(s, 'app_item', dict(A4=0, B4=0), 0xc0780000)
    check('card check fails: RUN APP returns its value, nothing loaded', end == 0xc0780000 and s.R['A4'] == 5 and not L and ok)
# ---- SD CARD menu (static, in the image): new 6-word list in the cave, count 5
import r8fw
from faderbar import file_map
off = file_map(bytearray(img)); wi = lambda ad: struct.unpack_from('<I', img, off(ad, 4))[0]
A = UM.patch.addrs
newl = wi(AM.SD_DESC + 32)
lst = [wi(newl + 4 * i) for i in range(6)]
nm = lambda p: img[off(p, 16):off(p, 16) + 16].split(b'\0')[0].decode()
check('SD CARD: count 5, descriptor points at the new list: ' + ', '.join(nm(wi(p)) for p in lst[:5]), wi(AM.SD_DESC) == 5 and newl == A['new_list']
      and lst[:3] == [0xc06dfeb8, AM.FORMAT_REC, 0xc06dff58] and lst[3:] == [A['rec_app'], A['rec_util'], 0]
      and [nm(wi(p)) for p in lst[:5]] == ['EXCHANGE', 'FORMAT', 'REMAIN', 'RUN APP', 'RUN UTIL'])
fr = img[off(AM.FORMAT_REC, 80):off(AM.FORMAT_REC, 80) + 80]
for name, recad, h in (('RUN APP', A['rec_app'], 'app_item'), ('RUN UTIL', A['rec_util'], 'util_item')):
    rr = img[off(recad, 80):off(recad, 80) + 80]
    check(f'{name} record = FORMAT record with name, type 0 and handler {h}', struct.unpack_from('<I', rr, 16)[0] == a.labels[h]
          and struct.unpack_from('<I', rr, 4)[0] == 0 and rr[8:16] == fr[8:16] and rr[20:] == fr[20:])
stock_off = file_map(bytearray(stock)); ws = lambda ad: struct.unpack_from('<I', stock, stock_off(ad, 4))[0]
check('the stock SD CARD list and the next menu list (SYSTEM) are untouched', [wi(AM.SD_LIST + 4 * i) for i in range(5)] == [ws(AM.SD_LIST + 4 * i) for i in range(5)])
check('the rest of the descriptor is unchanged', [wi(AM.SD_DESC + 4 * i) for i in range(12) if i not in (0, 8)] == [ws(AM.SD_DESC + 4 * i) for i in range(12) if i not in (0, 8)])
check('image checksum valid', struct.unpack_from('<I', img, 0x34)[0] == r8fw.checksum(bytearray(img)))
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
