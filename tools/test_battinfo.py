#!/usr/bin/env python3
"""BATTINFO.BIN (cbuild/utils/battinfo.c) in sim674 with the stock functions mocked (r8mock.py). Sets stock's state
words (BATTERY setting 0xC05B60C8, C1 at B14[612], USB flag B14[593], level B14[590]) and the GP6[1] pin, opens the
utility, and reads each row's value as the stock row drawer would (record +32 function). Two states: on USB with
C1 = 249, and on batteries with C1 = 327, setting 1, level 4. Checks the labels, the values, and that the rows are
read-only for the wheel and nothing in stock's state is written."""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock, mkutil
SCRATCH = 0xC0080000        # the row drawer's structure for a record's +32 value function (word 3 = the record's +28)
def nested(sim, fn, a4=None, stop=None):   # as in test_fileinfo.py: call fn inside a running simulation
    R = list(sim.R); pend, br = sim.pend, sim.branches
    sim.pend, sim.branches = [], []
    sim.R[S.ridx(1, 15)] = (R[S.ridx(1, 15)] - 0x800) & ~7
    if a4 is not None: sim.R[4] = a4
    stop = stop or 0xcafe1000 + 16 * len(R) % 0x100
    sim.R[S.ridx(1, 3)] = stop
    sim.run(fn, stop)
    ret = sim.R[4]
    sim.R[:] = R; sim.pend, sim.branches = pend, br
    return ret
W = os.path.join(HERE, '..')
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
img, base, entry, top = mkutil.pack(os.path.join(W, 'cbuild', 'utils', 'out', 'battinfo.out'))
B14 = 0xC0812B20
def run(setting, c1, usb, level, pin):
    sim = S.Sim(); sim.write(base, img[32:])
    env = r8mock.R8Env(sim, {})
    sim.wr32(0xC05B60C8, setting); sim.wr32(B14 + 4 * 612, c1); sim.wr32(B14 + 4 * 593, usb); sim.wr32(B14 + 4 * 590, level)
    gpio = {0x01E26098: pin << 1}
    old_io = sim.io.get(0x01E26)
    sim.io[0x01E26] = lambda s, a, size, v: gpio.get(a, 0) if v is None else None
    before = [sim.rd32(a) for a in (0xC05B60C8, B14 + 4 * 612, B14 + 4 * 593, B14 + 4 * 590)]
    rd = lambda a, n: struct.unpack('<%dI' % n, sim.read(a, 4 * n))
    seen = []
    def menu(s):
        d = rd(s.arg(0), 12)
        rows = []
        for r in rd(d[8], d[0]):
            rec = rd(r, 20)
            sim.write(SCRATCH, struct.pack('<4I', 0, 0, 0, rec[7]))
            rows.append((s.cstr(rec[0]), s.cstr(nested(sim, rec[8], SCRATCH)), rec[6]))
        seen.append((s.cstr(d[1]), rows)); s.R[4] = 0
    sim.mocks[r8mock.ADDR['r8_menu_engine']] = menu
    rc = sim.call(entry, (0, 0))
    after = [sim.rd32(a) for a in (0xC05B60C8, B14 + 4 * 612, B14 + 4 * 593, B14 + 4 * 590)]
    return rc, seen, before == after
rc, seen, same = run(0, 249, 1, 0, 1)
print('  USB:', seen)
rows = seen[0][1] if seen else []
check('title BATTERY, rows SETTING C1 VOLTS POWER GP6[1] LEVEL', seen and seen[0][0] == 'BATTERY' and [r[0] for r in rows] == ['SETTING', 'C1', 'VOLTS', 'POWER', 'GP6[1]', 'LEVEL'])
check('on USB: 0, 249, 5.27V, USB, 1 (USB), 0', [r[1] for r in rows] == ['0', '249', '5.27V', 'USB', '1 (USB)', '0'])
check('rows read-only for the wheel (record +24 bit 3); stock state not written; returns 0', all(r[2] & 8 for r in rows) and same and rc == 0)
rc, seen, same = run(1, 327, 0, 4, 0)
print('  batteries:', seen)
check('on batteries: 1, 327, 6.78V, BATTERY, 0, 4', [r[1] for r in seen[0][1]] == ['1', '327', '6.78V', 'BATTERY', '0', '4'])
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
