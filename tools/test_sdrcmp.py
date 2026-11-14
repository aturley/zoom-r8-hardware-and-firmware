#!/usr/bin/env python3
"""SD-RCMP / SD-FLAGS / SD-CLK (cbuild/forth/main.c, sd_read_long in sd.c) in sim674. SD-RCMP reads n blocks one at
a time and with one CMD18, and compares. The card model drops byte 4094 of any multi-block read of 4 KB or more sent
without DMATRIG, as the R8's controller does (measured 2026-11-14). With the default experiment flags (DCLR + PPLEN)
a 7-block long read must report "same" and 8 or 9 blocks "differ at 4094"; with SD-FLAGS 10000 (DMATRIG, as stock)
64 blocks must be "same". The board model has no EDMA, so mode 1 (DMA) is not exercised here. Nothing may be written."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
C = r8sd.SdModel(sim, blocks=65536, hc=True)
sim.mocks[syms['delay_us']] = lambda s: None
R = {}
def serial(m, text, until=' ok', timeout=400_000_000):
    n0 = len(m.in_data); m.out_q += text.encode()
    yield m.until(lambda: until.encode() in m.in_data[n0:], f'serial {text!r}', timeout)
    return m.in_data[n0:].decode('latin1')
def script(m):
    r = {}; yield from r8usb.mac_enumeration(r)(m); R['enum'] = r
    yield m.until(lambda: b'running word' in m.in_data, 'banner', 100_000_000)
    R['init'] = yield from serial(m, 'SD-INIT .\r')
    R['clk0'] = C.r.get(0x04, 0)
    R['seven'] = yield from serial(m, '100 7 0 SD-RCMP .\r')
    R['f7'] = C.read_faults
    R['eight8'] = yield from serial(m, '150 8 0 SD-RCMP .\r')
    R['eight'] = yield from serial(m, '200 9 0 SD-RCMP .\r')
    R['f8'] = C.read_faults
    R['flags_bad'] = yield from serial(m, 'HEX 1 SD-FLAGS DECIMAL\r')
    R['flags_ok'] = yield from serial(m, 'HEX 10000 SD-FLAGS DECIMAL\r')
    R['xflags'] = sim.read(syms['sd_xflags'], 4)
    R['stock64'] = yield from serial(m, '300 64 0 SD-RCMP .\r')
    R['refuse'] = yield from serial(m, '100 1 0 SD-RCMP .\r')
    R['refuse2'] = yield from serial(m, '100 8 2 SD-RCMP .\r')
    R['clk_bad'] = yield from serial(m, '2 SD-CLK\r')
    R['clk3'] = yield from serial(m, '4 SD-CLK\r'); R['clk3v'] = C.r.get(0x04, 0)
    R['seven3'] = yield from serial(m, '400 7 0 SD-RCMP .\r')
    R['reinit'] = yield from serial(m, 'SD-INIT .\r'); R['clk4v'] = C.r.get(0x04, 0)
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
print(f'  {sim.cycle:,} cycles; host errors: {U.errors}; card errors: {C.errors}')
for k in ('seven', 'eight', 'stock64', 'refuse', 'refuse2', 'flags_bad', 'clk_bad', 'seven3'): print(f'  {k}: {R.get(k, "")!r}')
u32 = lambda b: int.from_bytes(b or b'\0\0\0\0', 'little')
check('boots and the card initialises', R.get('enum', {}).get('done') and ' 0  ok' in R.get('init', ''))
check('7 blocks in one CMD18 (3.5 KB): "same", result -1, no fault', 'same' in R.get('seven', '') and '-1  ok' in R.get('seven', '') and R.get('f7') == 0)
check('9 blocks in one CMD18 (4.5 KB): the modelled fault is caught at byte 4094', 'differ at 4094' in R.get('eight', '') and R.get('f8', 0) >= 1)
check('8 blocks (exactly 4 KB) without DMATRIG: "differ at 4094"', 'differ at 4094' in R.get('eight8', ''))
check('both timings are printed', R.get('seven', '').count('us ') >= 2 and 'safe' in R['seven'] and 'long' in R['seven'])
check('SD-FLAGS refuses other bits and accepts DMATRIG (10000)', 'only 80' in R.get('flags_bad', '') and u32(R.get('xflags')) == 0x10000)
check('64 blocks with DMATRIG (SD-FLAGS 10000): "same"', 'same' in R.get('stock64', '') and '-1  ok' in R.get('stock64', ''))
check('bad n or mode refused with -2', '-2  ok' in R.get('refuse', '') and '-2  ok' in R.get('refuse2', ''))
check('SD-CLK: 2 refused, 4 sets 18 MHz (CLKRT 4), a 7-block compare still works', '3..255' in R.get('clk_bad', '') and R.get('clk3v', 0) & 0xFF == 4 and 'same' in R.get('seven3', ''))
check('SD-INIT puts the clock back to CLKRT 3 (22.5 MHz)', R.get('clk4v', 0) & 0xFF == 3 and R.get('clk0', 0) & 0xFF == 3)
check('nothing was written to the card', not C.writes and C.blocks_written == 0)
check('no card protocol errors', not C.errors)
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
