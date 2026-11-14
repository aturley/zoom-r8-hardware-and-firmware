#!/usr/bin/env python3
"""R8 Forth app (cbuild/forth) in sim674 with the board models and the USB0 model + scripted host (r8usb.py):
PHY bring-up as stock, enumeration like a Mac, CDC open, then Forth over the serial port, Ctrl-C, long output,
the LCD status rows, and power-off. Writes forth_lcd.png to TMPDIR."""
import os, sys, struct, time, re
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb
W = os.path.join(HERE, '..')
K = 50                                                    # TSCL runs 50x fast: status redraws and timeouts shrink
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

sim = S.Sim()
base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=K)
U = r8usb.Musb(sim, B)
sim.mocks[syms['delay_us']] = lambda s: None
res = {}
U.start(r8usb.mac_enumeration(res))
class Stop(Exception): pass
# the test conversation runs as a coroutine stepped from the trace hook, inside one simulator run
out = {}
def talk(text, wait_for, cycles=60_000_000):
    n0 = len(U.in_data); U.out_q += text.encode('latin1')
    deadline = sim.cycle + cycles
    while wait_for.encode() not in U.in_data[n0:] and sim.cycle < deadline: yield
    return U.in_data[n0:].decode('latin1')
def flow():
    deadline = sim.cycle + 400_000_000
    while not (res.get('done') or U.errors) and sim.cycle < deadline: yield
    out['enum_cycles'] = sim.cycle
    while b'Ctrl-C stops a running word' not in U.in_data and sim.cycle < deadline: yield   # sent on DTR, maybe already
    out['banner'] = U.in_data.decode('latin1')
    out['add'] = yield from talk('1 2 + .\r', ' ok')
    out['def'] = yield from talk(': SQ DUP * ;\r', 'ok')
    out['sq'] = yield from talk('7 SQ .\r', 'ok')
    out['loop'] = yield from talk(': T 5 0 DO I . LOOP ; T\r', 'ok')
    out['if'] = yield from talk(': SGN DUP 0< IF DROP -1 ELSE 0> IF 1 ELSE 0 THEN THEN ; -5 SGN . 0 SGN . 9 SGN .\r', 'ok')
    out['peek'] = yield from talk('HEX 11800000 @ .X 11800000 C@ . DECIMAL\r', 'ok')
    out['var'] = yield from talk('VARIABLE V 1234 V ! V @ . 5 V +! V @ .\r', 'ok')
    out['undef'] = yield from talk('FOO\r', '?')
    out['under'] = yield from talk('DROP\r', 'empty')
    out['spin'] = yield from talk(': SPIN BEGIN AGAIN ; SPIN\r', 'SPIN')
    out['ctrlc'] = yield from talk('\x03', 'abort')
    out['after'] = yield from talk('2 3 * .\r', 'ok')
    out['dump'] = yield from talk('HEX 11800000 200 DUMP DECIMAL\r', ' ok\r\n', 200_000_000)
    t_idle = sim.cycle + 3_000_000
    while sim.cycle < t_idle: yield                      # let the LCD catch up
    out['rows'] = lcd_rows(); out['packets'] = list(U.in_packets)
    B.png(os.path.join(os.environ.get('TMPDIR', '/tmp'), 'forth_lcd.png'))
    B.ext[0xB0] = 0
    deadline = sim.cycle + 20_000_000
    while B.pin(0x88, 16) and sim.cycle < deadline: yield
    raise Stop
F = flow()
n_tr = [0]
def tr(s, pc, pk):
    U.tick()
    n_tr[0] += 1
    if n_tr[0] & 15 == 0: next(F)
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
def lcd_rows():
    import zlib
    src = open(os.path.join(W, 'cbuild', 'forth', 'font.h')).read()
    glyphs = [bytes(int(x, 16) for x in m.group(1).split(',')) for m in re.finditer(r'\{(0x[^}]*)\}', src)]
    inv = {g: chr(32 + i) for i, g in enumerate(glyphs)}
    return [''.join(inv.get(bytes(B.lcd[r * 128 + 6 * c: r * 128 + 6 * c + 6]), '?') for c in range(21)) for r in range(8)]

t = time.time()
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 13)
except Stop: pass
print(f'  enumeration: {out.get("enum_cycles", 0):,} cycles; whole run {sim.cycle:,} cycles, {time.time() - t:.0f} s; host errors: {U.errors}')
st = {k: v.get('status') for k, v in res.items() if isinstance(v, dict)}
print('  ', st)
check('USB0 PSC sequence as stock: reset, on, clock off for PHY setup, on', [v for a, v in getattr(B, 'psc_log', []) if a == 0x01E27A04] == [0, 3, 2, 3])
check('PHY brought up as stock, then forced device mode (CFGCHIP2 0xEF00 -> 0x0932 -> 0x4932, reset pulse, clock good)', 0x0932 in U.cfg_writes and U.cfg == 0x4932 and any(v & 0x8000 for v in U.cfg_writes) and U.phy_up())
dev = res.get('dev', {}).get('data', b'')
check('device descriptor: USB 2.0, composite (IAD) class, EP0 64, VID 1209 PID 0001', dev[:8] == bytes([18, 1, 0, 2, 0xEF, 2, 1, 64]) and dev[8:12] == bytes([9, 0x12, 1, 0]))
check('short first read (64 asked) returns 18 bytes, then SET_ADDRESS works', res.get('dev64', {}).get('data', b'')[:18] == dev and st.get('addr') == 'ok' and st.get('dev') == 'ok' and U.faddr == 7)
cfg = res.get('cfg', {}).get('data', b'')
check('configuration descriptor: 98 bytes (two packets), CDC ACM + data interface + mass storage, EP 81/02/83',
      len(cfg) == 98 and cfg[:4] == bytes([9, 2, 98, 0]) and res['cfg9']['data'] == cfg[:9] and bytes([7, 5, 0x81, 2, 64, 0]) in cfg and bytes([7, 5, 0x02, 2, 64, 0]) in cfg and bytes([7, 5, 0x83, 3, 16, 0]) in cfg)
s2 = res.get('str2', {}).get('data', b'')
check('strings: language 0409, product "R8 Forth", serial "R8F1"', res.get('str0', {}).get('data') == bytes([4, 3, 9, 4]) and s2[2::2].decode() == 'R8 Forth' and res['str3']['data'][2::2].decode() == 'R8F1')
check('device qualifier STALLs (full speed only), then the next request works', st.get('qual') == 'stall' and st.get('status') == 'ok')
check('SET_CONFIGURATION 1: endpoints set up (64/64/16, toggles cleared), GET_CONFIGURATION = 1',
      st.get('setcfg') == 'ok' and res['getcfg'].get('data') == b'\x01' and U.ep[1].txmaxp == 64 and U.ep[2].rxmaxp == 64 and U.ep[3].txmaxp == 16 and U.ep[1].clrdt and U.ep[2].clrdt)
check('FIFO RAM: EP1 TX 64@64, EP2 RX 64@128, EP3 TX 16@192', U.fifo_sz.get((0x62, 1)) == 3 and U.fifo_sz.get((0x64, 1)) == 8 and U.fifo_sz.get((0x63, 2)) == 3 and U.fifo_sz.get((0x66, 2)) == 16 and U.fifo_sz.get((0x62, 3)) == 1 and U.fifo_sz.get((0x64, 3)) == 24)
check('CDC: SET_LINE_CODING accepted and read back, DTR set', st.get('linecoding') == 'ok' and res['getline'].get('data') == bytes([0x00, 0xC2, 0x01, 0x00, 0, 0, 8]) and st.get('dtr') == 'ok')
check('no host-side protocol errors', not U.errors)

g = lambda k: out.get(k, '')
for k in ('add', 'sq', 'loop', 'if', 'peek', 'var', 'undef', 'ctrlc', 'after'): print(f'  {k:6s}>', repr(g(k)))
check('banner when the terminal opens', 'R8 Forth' in g('banner'))
check('"1 2 + ." echoes and prints 3 ok', g('add').lstrip('\r\n') == '1 2 + . 3  ok\r\n')
check('colon definition: 7 SQ . -> 49', g('def').endswith(' ok\r\n') and '49  ok' in g('sq'))
check('DO LOOP: 0 1 2 3 4', '0 1 2 3 4  ok' in g('loop'))
check('IF ELSE THEN: -1 0 1', '-1 0 1  ok' in g('if'))
w0 = struct.unpack_from('<I', img, 0)[0]
check('memory peek: @ and C@ of the app image, in hex', f'{w0:08X} {img[0]:X}  ok' in g('peek'))
check('VARIABLE ! @ +!', '1234 1239  ok' in g('var'))
check('unknown word reported', 'FOO ?' in g('undef'))
check('stack underflow reported', 'stack empty' in g('under'))
check('Ctrl-C stops an endless loop, prompt works afterwards', 'abort' in g('ctrlc') and '6  ok' in g('after'))
lines = re.findall(r'(118[0-9A-F]{5}: [^\r]*)', g('dump'))
pk = out.get('packets', [0])
check(f'DUMP of 512 bytes: 32 lines, correct bytes, 64-byte packets ({len(pk)} IN packets)',
      len(lines) == 32 and lines[1].startswith('11800010: ' + ' '.join(f'{b:02X}' for b in img[16:32])) and max(pk) == 64)
check('a full 64-byte packet never ends a burst without a following packet (ZLP or data)', all(not (a == 64 and b is None) for a, b in zip(pk, pk[1:] + [None])))
rows = out.get('rows', [''] * 8)
print('  LCD:'); [print('   |' + r + '|') for r in rows]
check('LCD status: FORTH OPEN, counters', rows[0].startswith('FORTH OPEN') and rows[1].startswith('S'))
check('power switch off: USB disconnected (SOFTCONN off), power hold GP7[0] low', not B.pin(0x88, 16) and not U.power & 0x40)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
