#!/usr/bin/env python3
"""App chooser (cbuild/chooser) in sim674. A:\\R8APPS holds the Forth app as FORTH.BIN, a corrupted copy as BAD.BIN,
a second good app as "a echo.bin" (the Forth app again, banner changed), plus files that must not be listed (a macOS
"._" file, notes.txt, a folder). Checks: the list (sorted, names shown), the selection moving with the cursor keys and
the wheel, a corrupted app refused with the reason on screen, ENTER starting the chosen app (it enumerates on USB and
knows its own path), the app's RELOAD reloading that same file, and EXIT (a second run) restarting via the watchdog."""
import os, sys, struct, zlib, re, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
forth = open(os.path.join(W, 'cbuild', 'forth', 'out', 'APP.BIN'), 'rb').read()
echo = bytearray(forth); i = echo.find(b'R8 Forth\x00'); echo[i:i + 8] = b'R8 Echo '
struct.pack_into('<I', echo, 20, zlib.crc32(bytes(echo[32:]))); echo = bytes(echo)
bad = bytearray(forth); bad[40000] ^= 0x55; bad = bytes(bad)
def card():
    F = fat32img.Fat32()
    F.mkdir('R8APPS'); F.mkdir('R8APPS/old')
    F.add('R8APPS/FORTH.BIN', forth); F.add('R8APPS/BAD.BIN', bad); F.add('R8APPS/a echo.bin', echo, fragment=True)
    F.add('R8APPS/._FORTH.BIN', bytes(4096)); F.add('R8APPS/notes.txt', b'not an app')
    F.add('APP.BIN', b'chooser stand-in')
    return F.image()
FONT = [bytes(int(x, 0) for x in m.group(1).split(',')) for m in re.finditer(r'\{([^{}]*)\}', open(os.path.join(W, 'cbuild', 'forth', 'font.h')).read().split('FONT6', 1)[1])]
INV = {g: chr(32 + i) for i, g in enumerate(FONT) if len(g) == 6}
def lcd_rows(B):
    rows, inv = [], []
    for r in range(8):
        cells = [bytes(B.lcd[r * 128 + c: r * 128 + c + 6]) for c in range(0, 126, 6)]
        plain = ''.join(INV.get(x, '#') for x in cells)
        flipped = ''.join(INV.get(bytes(b ^ 0xFF for b in x), '#') for x in cells)
        if plain.count('#') > flipped.count('#'): rows.append(flipped.rstrip()); inv.append(True)
        else: rows.append(plain.rstrip()); inv.append(False)
    return rows, inv
FORTH_SYMS = S.load_elf(S.Sim(), os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))[2]
def run(script_fn, usb=False, max_s=3.0):
    blocks, nblk = card()
    sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'chooser', 'out', 'chooser.out'))
    B = r8board.Board(sim, clock=False, time_scale=50)
    C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=blocks)
    U = r8usb.Musb(sim, B, vbus_sensed=False)
    sim.mocks[syms['delay_us']] = lambda s: None
    sim.mocks[FORTH_SYMS['delay_us']] = lambda s: None
    sim.write(0xC05B60C8, struct.pack('<I', 1))          # stock's BATTERY setting: Ni-MH   # the launched app's own copy (else its real delays outlast the host's wait)
    out = {}
    G = script_fn(sim, B, U, out)
    class Stop(Exception): pass
    n = [0]
    def tr(s, pc, pk):
        U.tick(); n[0] += 1
        if pc >= 0xC0000000 and syms['delay_us'] in sim.mocks:
            del sim.mocks[syms['delay_us']]                  # trampoline running: the app
            sim.write(0xC05B60C8, struct.pack('<I', 0))      # and a previous app's buffers overwrote stock's setting
        # is about to overwrite L2, and the chooser's delay_us address then lies inside the app's code (it hit status())
        if n[0] & 31 == 0:
            try: next(G)
            except StopIteration: raise Stop
    sim.trace = tr
    for r in range(64): sim.R[r] = 0
    sim.R[S.ridx(1, 3)] = 0xcafe0000
    try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 12)
    except Stop: pass
    return sim, B, U, C, out
def waits(sim, B, sec):
    end = sim.cycle + sec * B.cpu_hz
    while sim.cycle < end: yield
def key(sim, B, st): B.send(sim.cycle / B.cpu_hz + 0.001, [st, 3]); B.send(sim.cycle / B.cpu_hz + 0.02, [st, 4])
# ---- run 1: list, navigation, a bad app, launch, the app's RELOAD
def script1(sim, B, U, out):
    w = lambda sec: U.delay(int(sec * B.cpu_hz))          # inside a host script: the model's own wait objects
    yield w(0.6); out['lcd0'] = lcd_rows(B)
    key(sim, B, 0x95); yield w(0.1); out['lcd1'] = lcd_rows(B)                         # DOWN: BAD.BIN
    key(sim, B, 0x98); yield w(4.0); out['lcd_bad'] = lcd_rows(B)                      # ENTER: refused (read + CRC of 98 KB)
    B.send(sim.cycle / B.cpu_hz + 0.001, [0xD0, 0x7F]); yield w(0.1); out['lcd_wheel'] = lcd_rows(B)   # wheel back: a echo.bin
    key(sim, B, 0x95); yield w(0.05); key(sim, B, 0x95); yield w(0.1); out['lcd2'] = lcd_rows(B)    # down twice: FORTH.BIN
    key(sim, B, 0x98)                                                                   # ENTER: run it
    r = {}
    yield from r8usb.mac_enumeration(r)(U)
    out['enum'] = r
    yield U.until(lambda: b'running word' in U.in_data, 'banner', 300_000_000)
    out['banner'] = U.in_data.decode('latin1')
    out['launched'] = sim.read(0xC03FFF04, 32).split(b'\0')[0].decode('latin1')
    out['battcopy'] = struct.unpack('<2I', sim.read(0xC03FFFE0, 8)); out['batt_type'] = sim.rd32(FORTH_SYMS['batt_type'])
    n0 = len(U.in_data); U.out_q += b'RELOAD\r'
    yield U.until(lambda: b'restarting' in U.in_data[n0:] or b'RELOAD:' in U.in_data[n0:], 'reload', 300_000_000)
    out['reload'] = U.in_data[n0:].decode('latin1')
def drive(sim, B, U, out):
    G = None
    def gen():
        g = script1(sim, B, U, out)
        yield from g
    return gen()
def script1_runner(sim, B, U, out):
    U.start(lambda m: script1(sim, B, U, out))
    while not out.get('reload') and not U.errors and sim.cycle < 3 * 10 ** 9: yield
t0 = time.time()
sim, B, U, C, out = run(script1_runner)
print(f'  run 1: {sim.cycle:,} cycles in {time.time() - t0:.0f} s; host errors {U.errors}')
for k in ('lcd0', 'lcd1', 'lcd_bad', 'lcd2'):
    rows, inv = out.get(k, ([], []))
    print(f'  {k}:'); [print(f'   {"*" if iv else " "}|{r}|') for r, iv in zip(rows, inv)]
rows, inv = out.get('lcd0', ([''] * 8, [False] * 8))
check('lists the three apps by file name, sorted; skips the "._" file, notes.txt and the folder',
      rows[1].strip() == 'a echo.bin' and rows[2].strip() == 'BAD.BIN' and rows[3].strip() == 'FORTH.BIN' and rows[4].strip() == '')
check('header shows the position (1/3); the first app is selected; key hint at the bottom', rows[0].startswith('R8 APPS') and rows[0].endswith('1/3') and inv[1] and not inv[2] and 'ENTER run' in rows[7])
rows, inv = out.get('lcd1', ([''] * 8, [False] * 8))
check('DOWN moves the selection (2/3)', inv[2] and not inv[1] and rows[0].endswith('2/3'))
rows, inv = out.get('lcd_bad', ([''] * 8, [False] * 8))
check('ENTER on a corrupted app: refused, reason on screen, chooser keeps running', 'CRC mismatch' in rows[7] and inv[2])
rows, inv = out.get('lcd_wheel', ([''] * 8, [False] * 8))
check('the wheel moves the selection too (back to 1/3)', inv[1] and rows[0].endswith('1/3'))
rows, inv = out.get('lcd2', ([''] * 8, [False] * 8))
check('selection on FORTH.BIN (3/3)', inv[3] and rows[0].endswith('3/3'))
check('ENTER starts it: the Forth app enumerates on USB and prints its banner', out.get('enum', {}).get('done') and 'R8 Forth' in out.get('banner', ''))
check(f'the chooser saved stock\'s BATTERY setting ({out.get("battcopy")}) and the app uses it although stock\'s word was overwritten (batt_type {out.get("batt_type")})',
      out.get('battcopy') == (0x54423852, 1) and out.get('batt_type') == 1)
check(f'the app knows its path ({out.get("launched")!r})', out.get('launched') == 'R8APPS/FORTH.BIN')
check('the app\'s RELOAD reloads that same file', 'R8APPS/FORTH.BIN ok, restarting' in out.get('reload', ''))
# ---- run 2: EXIT restarts via the watchdog
def script2(sim, B, U, out):
    yield from waits(sim, B, 0.6)
    key(sim, B, 0x93)
    yield from waits(sim, B, 0.3)
    out['wd'] = B.watchdog_reset; out['lcd'] = lcd_rows(B)
sim, B, U, C, out = run(script2)
rows, inv = out.get('lcd', ([''] * 8, [False] * 8))
check(f'EXIT shows "restarting..." and arms the watchdog (chip reset into the stock firmware)', out.get('wd') is not None and 'restarting' in rows[7])
check('no card protocol errors', not C.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
