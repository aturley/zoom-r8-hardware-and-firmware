#!/usr/bin/env python3
"""R8 Forth connect strategies: with the controller blind to VBUS (as on the R8: DEVCTL 0x80), the app must cycle
A (stock) -> B (session request) -> C (PHY forced to device mode), get enumerated, then stay put and work."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(HERE, '..', 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
sim.mocks[syms['delay_us']] = lambda s: None
res = {}; U.start(r8usb.mac_enumeration(res))
seen = []
class Stop(Exception): pass
n = [0]; t_done = [None]
def tr(s, pc, pk):
    U.tick(); n[0] += 1
    if n[0] & 255 == 0:
        st = (U.cfg >> 13) & 3, U.devctl & 1
        if not seen or seen[-1] != st: seen.append(st)
    if res.get('done') and t_done[0] is None: t_done[0] = s.cycle; U.out_q += b'2 3 * .\r'
    if t_done[0] and (b' ok' in U.in_data or s.cycle > t_done[0] + 60_000_000): raise Stop
    if s.cycle > 600_000_000: raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 13)
except Stop: pass
print('  (OTG mode, session bit) over time:', seen, ' errors:', U.errors)
check('starts in C (forced device + session) and never needs another strategy', seen[-1] == (2, 1) and (0, 1) not in seen)
check('enumerated after C', res.get('done') and all(v.get('status') in ('ok', 'stall') for v in res.values() if isinstance(v, dict)))
check('stays on C once reset by the host (no more reconnects)', seen[-1] == (2, 1) and U.power & 0x40)
check('serial works: 2 3 * . -> 6 ok', b'6  ok' in U.in_data)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
