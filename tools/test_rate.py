#!/usr/bin/env python3
"""RATE / .RATE (cbuild/forth clock.c) in sim674: the Forth app reads the board ID straps (7, as on the user's R8)
and switches the audio clock by writing the I2C clock chip at 0x54 exactly as stock 1.17 does (F7 <- 10, then F5 <-
11 bytes). Unsupported rates are refused, and a chip that does not acknowledge leaves the rate unchanged."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
STOP = [0x54, 0xF7, 0x10]
F44 = [0x54, 0xF5, 0x01, 0x0E, 0x00, 0x00, 0x05, 0x3B, 0x72, 0x41, 0xC5, 0x1C, 0x00]
F48 = [0x54, 0xF5, 0x01, 0x0E, 0x00, 0x00, 0x05, 0x3B, 0x72, 0x47, 0x93, 0x1D, 0x03]
sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
sim.mocks[syms['delay_us']] = lambda s: None
R = {}
def serial(m, text, until=' ok', timeout=200_000_000):
    n0 = len(m.in_data); m.out_q += text.encode()
    yield m.until(lambda: until.encode() in m.in_data[n0:], f'serial {text!r}', timeout)
    return m.in_data[n0:].decode('latin1')
def script(m):
    r = {}; yield from r8usb.mac_enumeration(r)(m); R['enum'] = r
    yield m.until(lambda: b'running word' in m.in_data, 'banner', 100_000_000)
    R['show0'] = yield from serial(m, '.RATE\r')
    R['set48'] = yield from serial(m, '48000 RATE .RATE\r'); R['log48'] = list(B.i2c_log); R['fs48'] = B.fs
    R['bad'] = yield from serial(m, '22050 RATE .RATE\r'); R['logbad'] = len(B.i2c_log)
    R['set44'] = yield from serial(m, '44100 RATE .RATE\r'); R['log44'] = list(B.i2c_log); R['fs44'] = B.fs
    B.i2c_ack = set()
    R['nack'] = yield from serial(m, '48000 RATE .RATE\r'); R['lognack'] = len(B.i2c_log)
    B.i2c_ack = {0x54}
    R['raw'] = yield from serial(m, 'HEX 47 31D90 CLK-RAW DECIMAL\r')
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
for k in ('show0', 'set48', 'bad', 'set44', 'nack', 'raw'): print(f'  {k}: {R.get(k, "")!r}')
check('boots; .RATE reports 44100 Hz and board 7', R.get('enum', {}).get('done') and '44100 Hz, board 7' in R.get('show0', ''))
check('48000 RATE writes stop then the 48 kHz setting, as stock', R.get('log48') == [STOP, F48] and R.get('fs48') == 48000 and '48000 Hz' in R.get('set48', ''))
check('22050 RATE is refused, nothing written, rate stays 48000', '44100 or 48000 only' in R.get('bad', '') and R.get('logbad') == 2 and '48000 Hz' in R.get('bad', ''))
check('44100 RATE writes stop then the 44.1 kHz setting', R.get('log44') == [STOP, F48, STOP, F44] and R.get('fs44') == 44100 and '44100 Hz' in R.get('set44', ''))
check('a chip that NACKs: reported, rate unchanged, nothing logged', 'NACK' in R.get('nack', '') and '44100 Hz' in R.get('nack', '') and R.get('lognack') == 4)
check('CLK-RAW sends the given family byte and 18-bit value', B.i2c_log[-1] == [0x54, 0xF5, 1, 0x0E, 0, 0, 5, 0x3B, 0x72, 0x47, 0x90, 0x1D, 0x03])
check(f'no START lost to an unfinished STOP ({B.i2c_lost})', B.i2c_lost == 0)
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
