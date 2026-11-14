#!/usr/bin/env python3
"""PANEL-MON / PANEL-TX (cbuild/forth main.c) in sim674: messages the panel sends are printed decoded with the panel
map's names (keys, faders with running status, wheel, pads, C1), and PANEL-TX puts raw bytes on the panel link."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
sim.mocks[syms['delay_us']] = lambda s: None
R = {}
def serial(m, text, until, timeout=300_000_000):
    n0 = len(m.in_data); m.out_q += text.encode()
    yield m.until(lambda: until.encode() in m.in_data[n0:], f'serial {text!r}', timeout)
    return m.in_data[n0:].decode('latin1')
def script(m):
    r = {}; yield from r8usb.mac_enumeration(r)(m)
    yield m.until(lambda: b'running word' in m.in_data, 'banner', 100_000_000)
    R['faders'] = yield from serial(m, 'FADERS\r', ' ok')
    t = sim.cycle / B.cpu_hz
    for dt, msg in ((0.5, [0xA0, 3]), (0.6, [0xA0, 4]), (0.7, [0x98, 3]), (0.8, [0xC3, 10, 20, 30]), (0.9, [0xD0, 1, 0x7F]),
                    (1.0, [0xF0, 0, 1, 0x57]), (1.1, [0xC1, 1, 0x78]), (1.2, [0xCB, 99]), (1.3, [0xA3, 3])):
        B.send(t + dt, msg)
    R['mon'] = yield from serial(m, '2 PANEL-MON\r', ' ok')
    n0 = len(B.tx)
    R['tx'] = yield from serial(m, 'HEX HERE 89 C, 1 C, 8 C, 1 C, 2 C, 4 C, 8 C, 10 C, 20 C, 40 C, 80 C, F7 C, HERE OVER - PANEL-TX DECIMAL\r', ' ok')
    R['txbytes'] = bytes(B.tx[n0:])
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
print(R.get('mon', '')); print('  tx:', R.get('txbytes', b'').hex(' '), ' errors', U.errors)
mon = R.get('mon', '')
for want in ('A0 03  STOP press', 'A0 04  STOP release', '98 03  ENTER press', 'C3 0A 14 1E  fader 1 = 30', 'D0 01 7F  wheel 1  -1',
             'F0 00 01 57  pad 1 velocity 87', 'C1 01 78  C1 14-bit = 248', 'CB 63  MASTER fader = 99', 'A3 03  SOFT1 press'):
    check(f'PANEL-MON shows {want!r}', want in mon)
check('PANEL-TX sends the bytes as given', R.get('txbytes') == bytes([0x89, 1, 8, 1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 0xF7]))
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
