#!/usr/bin/env python3
"""RELOAD (cbuild/forth reload.c + tramp.asm) in sim674: the Forth app reads A:\\APP.BIN from a FAT32 card,
refuses a corrupted one (CRC) and keeps running, then loads a good one (the same app with its banner changed to
"R9 Forth", CRC fixed) over itself from a trampoline in DDR. The new app must re-enumerate on USB, print the new
banner and start with a fresh dictionary. The board model drops decoded instructions only on L1PINV, so a missing
cache invalidate shows up here as the old code running."""
import os, sys, struct, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

app = bytearray(open(os.path.join(W, 'cbuild', 'forth', 'out', 'APP.BIN'), 'rb').read())
i = app.find(b'R8 Forth\x00'); assert i > 0             # banner title, core fallback, USB product name
while i > 0: app[i + 1] = ord('9'); i = app.find(b'R8 Forth\x00')   # "R9 Forth": visibly the new image
struct.pack_into('<I', app, 20, zlib.crc32(bytes(app[32:])))
good = bytes(app)
bad = bytearray(good); bad[40000] ^= 0x55                # one byte off: CRC must fail
F = fat32img.Fat32()
F.mkdir('ZOOM_R8')
blocks_of = F.add('APP.BIN', bytes(bad))
image, nblk = F.image()
bad_blk = blocks_of[40000 // (512 * F.spc)] + (40000 % (512 * F.spc)) // 512

sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=image)
sim.mocks[syms['delay_us']] = lambda s: None
R = {}
def serial(m, text, until, timeout=200_000_000):
    n0 = len(m.in_data); m.out_q += text.encode()
    yield m.until(lambda: until.encode() in m.in_data[n0:], f'serial {text!r}', timeout)
    return m.in_data[n0:].decode('latin1')
def script(m):
    r = {}; yield from r8usb.mac_enumeration(r)(m); R['enum1'] = r
    yield m.until(lambda: b'running word' in m.in_data, 'banner', 100_000_000)
    R['banner1'] = m.in_data.decode('latin1')
    R['mark'] = yield from serial(m, ': MARK 42 ; MARK .\r', ' ok')
    R['bad'] = yield from serial(m, 'RELOAD\r', ' ok')
    R['alive'] = yield from serial(m, '1 2 + .\r', ' ok')
    C.image[bad_blk] = good[(bad_blk - blocks_of[0]) * 512:(bad_blk - blocks_of[0]) * 512 + 512]   # repair the card
    ver, l2len = struct.unpack_from('<I', good, 4)[0], struct.unpack_from('<I', good, 12)[0]
    if ver == 2:                                            # wipe the DDR segment: only the trampoline can restore it
        a, n = struct.unpack_from('<2I', good, 32 + l2len); R['ddrseg'] = (a, n); sim.write(a, bytes(n))
    c0 = m.connected_at; inv0 = B.l1p_invalidates
    R['good'] = yield from serial(m, 'RELOAD\r', 'restarting')
    yield m.until(lambda: m.connected_at is not None and m.connected_at != c0, 'reconnect after RELOAD', 600_000_000)
    R['inv'] = B.l1p_invalidates - inv0
    m.bulk_on = False; m.msc_in.clear(); m.msc_out.clear(); m.out_q.clear(); m.in_data.clear()
    r = {}; yield from r8usb.mac_enumeration(r)(m); R['enum2'] = r
    yield m.until(lambda: b'running word' in m.in_data, 'banner after RELOAD', 100_000_000)
    R['banner2'] = m.in_data.decode('latin1')
    R['mark2'] = yield from serial(m, 'MARK .\r', '?')
    R['math2'] = yield from serial(m, '6 7 * .\r', ' ok')
    R['ddrinfo'] = yield from serial(m, 'DDR-INFO\r', ' ok')
    R['ddrcode'] = yield from serial(m, 'HEX 10000 SD-FLAGS 2 SD-CLK DECIMAL 7 .\r', ' ok')
    R['done'] = True
U.start(script)
class Stop(Exception): pass
def tr(s, pc, pk):
    U.tick()
    if R.get('done') or U.errors: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 12)
except Stop: pass
print(f'  {sim.cycle:,} cycles; host errors: {U.errors}')
for k in ('bad', 'alive', 'good', 'mark2', 'math2', 'ddrinfo', 'ddrcode'): print(f'  {k}: {R.get(k, "")!r}')
check('first boot: enumerates, banner "R8 Forth"', R.get('enum1', {}).get('done') and 'R8 Forth' in R.get('banner1', ''))
check('RELOAD with a corrupted APP.BIN refuses (CRC mismatch) and Forth keeps running',
      'CRC mismatch' in R.get('bad', '') and ' 3  ok' in R.get('alive', ''))
check('RELOAD with a good APP.BIN: says so, the device disconnects and re-enumerates', 'APP.BIN ok, restarting' in R.get('good', '') and R.get('enum2', {}).get('done'))
check(f'the program cache was invalidated before the jump ({R.get("inv")} L1PINV writes)', (R.get('inv') or 0) >= 1)
check('the new image runs: banner "R9 Forth"', 'R9 Forth' in R.get('banner2', ''))
check('fresh dictionary (MARK gone), Forth works', '?' in R.get('mark2', '') and ' 42  ok' in R.get('math2', ''))
check(f'the app file is version 2 with a DDR segment {tuple(hex(x) for x in R.get("ddrseg", ()))}, wiped before RELOAD', R.get('ddrseg'))
check('after RELOAD the DDR data is back (DDR-INFO ok): the trampoline copied the DDR segment', ' ok ' in R.get('ddrinfo', '') and 'WRONG' not in R.get('ddrinfo', ''))
check('DDR code runs after RELOAD (SD-FLAGS, SD-CLK are .ddrtext; SD-CLK answers "SD-INIT first")', 'SD-INIT first' in R.get('ddrcode', '') and ' 7  ok' in R.get('ddrcode', ''))
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
