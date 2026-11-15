#!/usr/bin/env python3
"""SD-WCMP (cbuild/forth/main.c, sd_write_long in sd.c) in sim674: one CMD25 of n blocks in the unpartitioned gap,
read back block by block, compared. The card here has its partition at LBA 4096 (not the usual 8192), so the word
must take its limit from the MBR. The card model inserts a zero at byte 4095 of any multi-block write of 4 KB or more sent
without DMATRIG, as the R8 does: with the default experiment flags (DCLR + PPLEN) 9 blocks must "differ at 4095",
with SD-FLAGS 10000 (DMATRIG, as stock) 7, 9 and the 8 blocks at the edge must be "same". Nothing at or past the partition start may be written."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd, fat32img
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
PART = 4096
F = fat32img.Fat32(part_lba=PART); F.mkdir('ZOOM_R8')
image, nblk = F.image()
sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
C = r8sd.SdModel(sim, blocks=nblk + 1024, hc=True, image=image)
before = [C.block(PART + k) for k in range(64)]
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
    R['flags'] = yield from serial(m, 'HEX 10000 SD-FLAGS DECIMAL\r')
    R['seven'] = yield from serial(m, '100 7 1 SD-WCMP .\r'); R['w7'] = list(C.writes)
    R['nine'] = yield from serial(m, '200 9 2 SD-WCMP .\r')
    R['f0'] = yield from serial(m, 'HEX 8080 SD-FLAGS DECIMAL\r')
    R['nine_old'] = yield from serial(m, '220 9 22 SD-WCMP .\r')
    R['f1'] = yield from serial(m, 'HEX 10000 SD-FLAGS DECIMAL\r')
    R['edge'] = yield from serial(m, f'{PART - 8} 8 3 SD-WCMP .\r'); R['wedge'] = list(C.writes)
    R['over'] = yield from serial(m, f'{PART - 7} 8 4 SD-WCMP .\r')
    R['zero'] = yield from serial(m, '0 4 5 SD-WCMP .\r')
    R['big'] = yield from serial(m, '300 129 6 SD-WCMP .\r')
    n0 = len(C.writes); R['busy'] = yield from serial(m, '400 16 3 SD-BUSY\r'); R['wbusy'] = C.writes[n0:]
    R['busy_over'] = yield from serial(m, f'{PART - 4} 8 1 SD-BUSY\r')
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
print(f'  {sim.cycle:,} cycles; host errors: {U.errors}; card errors: {C.errors}; writes: {C.writes}')
for k in ('seven', 'nine', 'nine_old', 'edge', 'over', 'zero', 'big', 'busy', 'busy_over'): print(f'  {k}: {R.get(k, "")!r}')
check('boots and the card initialises', R.get('enum', {}).get('done') and ' 0  ok' in R.get('init', ''))
check('7 blocks in one CMD25 at block 100: "same", -1', 'same' in R.get('seven', '') and '-1  ok' in R.get('seven', ''))
check('that was one 7-block write at block 100', R.get('w7') == [(100, 7)])
check('9 blocks with DMATRIG: "same"', 'same' in R.get('nine', ''))
check('9 blocks without DMATRIG (8080): the modelled fault is caught at byte 4095', 'differ at 4095' in R.get('nine_old', ''))
check(f'the last 8 blocks before the partition ({PART - 8}..{PART - 1}) are allowed and exact', 'same' in R.get('edge', '') and (PART - 8, 8) in R.get('wedge', []))
check('one block into the partition: refused (-2), names the gap from the MBR', '-2  ok' in R.get('over', '') and f'1..{PART - 1}' in R.get('over', ''))
check('block 0 (the MBR) refused', '-2  ok' in R.get('zero', ''))
check('n = 129 refused', '-2  ok' in R.get('big', ''))
check('every write ended before the partition', all(b + n <= PART for b, n in C.writes))
check('the first 64 partition blocks are unchanged', [C.block(PART + k) for k in range(64)] == before)
check(f'SD-BUSY 400 16 3: three 16-block writes at 400 ({R.get("wbusy")}), reports busy and throughput', R.get('wbusy') == [(400, 16)] * 3 and 'busy avg' in R.get('busy', '') and 'slowest write' in R.get('busy', ''))
check('SD-BUSY reaching into the partition is refused', 'only blocks 1..' in R.get('busy_over', '') and 'busy avg' not in R.get('busy_over', ''))
check('no card protocol errors', not C.errors)
check('no USB errors', not U.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
