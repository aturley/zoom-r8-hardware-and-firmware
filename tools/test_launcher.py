#!/usr/bin/env python3
"""App launcher (cbuild/launcher) in sim674: stock file API mocked, APP.BIN on the mock card.
Bad or missing files must give a popup and return without touching the machine; a good file must take the machine
over in order (interrupts, EDMA, McASP, L2 writeback-invalidate, L2 all SRAM, copy, L1D writeback, L1P invalidate)
and land in the app, which then runs: the chord synth reaches its title screen."""
import os, sys, struct, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock, r8board, celf, mkapp
W = os.path.join(HERE, '..')
LAUNCH_OUT = os.path.join(W, 'cbuild', 'launcher', 'out', 'launch.out')
SYNTH_OUT = os.path.join(W, 'cbuild', 'synth', 'out', 'synth.out')
NO_FILE, INVALID = 0xc06e77c5, 0xc06e785d
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

good, base, entry, top = mkapp.pack(SYNTH_OUT)
img = good[32:]

def setup(files):
    sim = S.Sim()
    _, _, syms, _ = S.load_elf(sim, LAUNCH_OUT)
    env = r8mock.R8Env(sim, files)
    pops = []
    def popup(s): pops.append(s.arg(0)); s.R[4] = 0
    sim.mocks[r8mock.ADDR['r8_popup']] = popup
    io = []                                                  # (cycle, addr, value) for every register write
    regs = {0x01840000: 0x3}                                 # L2CFG: stock-like, part of L2 is cache
    def h(sim, a, size, v):
        if v is None: return regs.get(a, 0)                  # cache operations complete at once (read back 0)
        io.append((sim.cycle, a, v))
        if a == 0x01840000: regs[a] = v
    for page in (0x01840, 0x01845, 0x01C00, 0x01C01, 0x01E30, 0x01E31, 0x01D00): sim.io[page] = h
    sim.write(0x11800000, b'\xee' * 0x30000)                 # L2: whatever the stock firmware left there
    return sim, syms, env, pops, io

def call(files):
    sim, syms, env, pops, io = setup(files)
    rc = sim.call(syms['r8_entry'], (0, 0))
    return rc, sim, env, pops, io

def bad(name, files, want_popup):
    rc, sim, env, pops, io = call(files)
    check(f'{name}: popup, back to the menu, machine untouched',
          rc == 0 and pops == [want_popup] and not io and not env.fds and sim.read(0x11800000, 16) == b'\xee' * 16)

def hdr(**kw):
    f = dict(zip(('magic', 'version', 'load', 'len', 'entry', 'crc', 'top', 'rsvd'), struct.unpack_from('<8I', good)))
    f.update(kw); return struct.pack('<8I', *f.values())

bad('no APP.BIN', {}, NO_FILE)
bad('empty file', {'A:\\APP.BIN': b''}, INVALID)
bad('bad magic', {'A:\\APP.BIN': hdr(magic=0x12345678) + img}, INVALID)
bad('bad version', {'A:\\APP.BIN': hdr(version=2) + img}, INVALID)
bad('corrupt image (CRC)', {'A:\\APP.BIN': good[:-1] + bytes([good[-1] ^ 1])}, INVALID)
bad('truncated image', {'A:\\APP.BIN': good[:-100]}, INVALID)
bad('too big for the staging area', {'A:\\APP.BIN': hdr(len=0x30004) + img}, INVALID)
bad('load address outside L2', {'A:\\APP.BIN': hdr(load=0xC0000000) + img}, INVALID)
bad('data reaches the app stack', {'A:\\APP.BIN': hdr(top=0x1182C004) + img}, INVALID)
bad('entry outside the image', {'A:\\APP.BIN': hdr(entry=base + len(img)) + img}, INVALID)

# ---- good file: run until control reaches the app's entry
big = bytes((i * 7 + 3) & 0xff for i in range(0x30000 - 0x4000))      # largest allowed image, several 32 KB reads
bigf = struct.pack('<8I', mkapp.MAGIC, 1, 0x11800000, len(big), 0x11800000, zlib.crc32(big), 0x1182C000, 0) + big
for name, f, body in (('largest image (176 KB)', bigf, big), ('chord synth', good, img)):
    sim, syms, env, pops, io = setup({'A:\\APP.BIN': f})
    for i in range(64): sim.R[i] = 0
    sim.R[S.ridx(1, 15)] = 0xc0f00000; sim.R[S.ridx(1, 3)] = 0xcafe0000
    sim.run(syms['r8_entry'], struct.unpack_from('<I', f, 16)[0], max_cycles=10 ** 9)
    check(f'{name}: image copied to L2 exactly', sim.read(0x11800000, len(body)) == body)
    check(f'{name}: file closed, no popup', not env.fds and not pops)
check('interrupts off (GIE clear, IER 0 apart from the always-set bit 0, nothing pending)',
      sim.gie == 0 and sim.ier & ~1 == 0 and sim.ifr == 0)
order = [a for c, a, v in io]
def first(a): return order.index(a) if a in order else 10 ** 9
ok = all(any(a == cc + 0x1028 and v == 0xFFFFFFFF for c, a, v in io) for cc in (0x01C00000, 0x01E30000))
check('EDMA events disabled on both channel controllers', ok)
check('McASP0 stopped (GBLCTL 0)', any(a == 0x01D00044 and v == 0 for c, a, v in io))
check('then L2 writeback-invalidate, L2 all SRAM, L1D writeback, L1P invalidate, in that order',
      max(first(0x01C01028), first(0x01E31028), first(0x01D00044)) < first(0x01845004) < first(0x01840000)
      < first(0x01845040) < first(0x01845028) < 10 ** 9 and [v for c, a, v in io if a == 0x01840000] == [0])

# ---- and the app runs: board models in place of the register log, synth from its entry
_, _, ssyms, _ = celf.image(SYNTH_OUT)
B = r8board.Board(sim, clock=True, time_scale=8)
sim.mocks[ssyms['delay_us']] = lambda s: None
class Done(Exception): pass
stop = int(0.4 * B.cpu_hz) + sim.cycle
def tr(s, pc, pk):
    if s.cycle > stop: raise Done
sim.trace = tr
try: sim.run(entry, 0xcafe0000, max_cycles=10 ** 12)
except Done: pass
import re
src = open(os.path.join(W, 'cbuild', 'synth', 'lcd.c')).read()
font = {bytes(int(x, 16) for x in m.group(2).split(',')): m.group(1) for m in re.finditer(r"\{'(.)',\{([^}]*)\}\}", src)}
title = ''.join(font.get(bytes(B.lcd[2 + 6 * i: 7 + 6 * i]), '?') for i in range(8))
print(f'  after launch: LCD reads "{title}", {len(B.samples)} audio slots, {B.late} late')
check('launched synth draws its title and streams audio without underruns', title == 'R8 CHORD' and len(B.samples) > 1000 and B.late == 0)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
