#!/usr/bin/env python3
"""SD block driver (cbuild/forth sd.c) through the R8 Forth words, in sim674 with the USB host and an SD card model:
SD-INIT on an SDHC card (and an SDSC card), SD-INFO, BLK + DUMP compared with the card image, out-of-range and
no-card errors, protocol rule checks in the card model."""
import os, sys, re, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
mbr = bytearray(512)
mbr[0x1BE:0x1CE] = bytes([0, 0, 0, 0, 0x0C, 0, 0, 0]) + struct.pack('<II', 8192, 65536 - 8192)
mbr[510:512] = b'\x55\xAA'; mbr[0:16] = b'R8 TEST CARD MBR'

def session(card_kw, lines):
    sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(HERE, '..', 'cbuild', 'forth', 'out', 'forth.out'))
    B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
    C = r8sd.SdModel(sim, **card_kw)
    sim.mocks[syms['delay_us']] = lambda s: None
    res = {}; U.start(r8usb.mac_enumeration(res))
    out = []
    class Stop(Exception): pass
    def talk(text, wait_for, cycles=80_000_000):
        n0 = len(U.in_data); U.out_q += text.encode(); dl = sim.cycle + cycles
        while not re.search(wait_for, U.in_data[n0:].decode('latin1')) and sim.cycle < dl: yield
        return U.in_data[n0:].decode('latin1')
    def flow():
        while not res.get('done') and sim.cycle < 300_000_000: yield
        yield from talk('', 'running word')
        for l in lines: out.append((yield from talk(l + '\r', r' ok\r\n|\?\r\n')))
        raise Stop
    F = flow(); n = [0]
    def tr(s, pc, pk):
        U.tick(); n[0] += 1
        if n[0] & 15 == 0: next(F)
    sim.trace = tr
    for r in range(64): sim.R[r] = 0
    sim.R[S.ridx(1, 3)] = 0xcafe0000
    try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=10 ** 13)
    except Stop: pass
    return out, C, U
def dump_bytes(text):
    b = bytearray()
    for m in re.finditer(r'[0-9A-F]{8}: ((?:[0-9A-F]{2} ){1,16})', text): b += bytes(int(x, 16) for x in m.group(1).split())
    return bytes(b)

out, C, U = session(dict(blocks=65536, hc=True, image={0: bytes(mbr)}),
                    ['SD-INIT .', 'SD-INFO', 'SD-BLOCKS .', 'HEX 0 BLK 200 DUMP DECIMAL', '12345 BLK 64 DUMP', '65535 BLK 16 DUMP',
                     '65536 BLK DROP', 'VARIABLE B 128 CELLS ALLOT 4321 B SD-READ . B 32 DUMP'])
for o in out[:3] + out[6:]: print('  >', repr(o[:120]))
print('  card log:', C.log[:14], '...', len(C.log), 'commands;', 'errors:', C.errors)
check('SD-INIT on SDHC returns 0', out[0].endswith('0  ok\r\n'))
check('SD-INFO: SDHC, 32MB, 65536 blocks, RCA 1234, name SU32G', 'SDHC' in out[1] and '65536' in out[1] and '1234' in out[1] and 'SU32G' in out[1])
check('init sequence: CMD0 CMD8 (CMD55 ACMD41)x3 CMD2 CMD3 CMD9 CMD7 CMD55 ACMD6',
      [c for c, a in C.log[:14]] == ['CMD0', 'CMD8', 'CMD55', 'ACMD41', 'CMD55', 'ACMD41', 'CMD55', 'ACMD41', 'CMD2', 'CMD3', 'CMD9', 'CMD7', 'CMD55', 'ACMD6'] and C.log[3][1] & 0x40000000)
check('block 0 (MBR) read exactly, all 512 bytes', dump_bytes(out[3]) == bytes(mbr))
check('block 12345 and the last block (65535) read exactly', dump_bytes(out[4]) == C.block(12345)[:64] and dump_bytes(out[5]) == C.block(65535)[:16])
check('block 65536 rejected without touching the card ("beyond the card")', 'beyond the card' in out[6])
check('SD-READ into a variable buffer', dump_bytes(out[7]) == C.block(4321)[:32] and 'DUMP 0 ' in out[7])
check('SDHC uses block addresses; 4-bit bus; no protocol errors', C.log[-1] == ('CMD17', 4321) and C.width == 4 and not C.errors)
check('fast clock for data: 22.5 MHz (CLKRT 3, as stock)', abs(C.clock_hz() - 22.5e6) < 1)

out, C, U = session(dict(blocks=32768, hc=False), ['SD-INIT .', 'SD-INFO', '100 BLK 32 DUMP'])
print('  SDSC:', repr(out[1][:80]), C.errors)
check('SDSC card: init, 16 MB / 32768 blocks, CMD16 512, byte addresses', out[0].endswith('0  ok\r\n') and '32768' in out[1] and 'SDSC' in out[1]
      and ('CMD16', 512) in C.log and ('CMD17', 100 * 512) in C.log and dump_bytes(out[2]) == C.block(100)[:32] and not C.errors)
out, C, U = session(dict(present=False), ['SD-INIT .', '0 BLK DROP'])
print('  no card:', repr(out[0][:80]))
check('no card: SD-INIT reports "no card" and returns 1, BLK reports it too, prompt stays alive', 'no card' in out[0] and out[0].endswith('1  ok\r\n') and 'no card' in out[1])
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
