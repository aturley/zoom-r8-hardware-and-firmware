#!/usr/bin/env python3
"""USB mass storage (cbuild/forth msc.c) in sim674: the composite device enumerates (IAD + CDC + MSC), then a host
script talks Bulk-Only Transport on EP4 like macOS: INQUIRY, TEST UNIT READY, READ CAPACITY, MODE SENSE (write
protect), READ(10) checked against the SD card model, out-of-range and write rejection with sense data, an unknown
command, invalid-CBW stall + reset recovery, and Forth on the serial port alongside."""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8board, r8usb, r8sd
from r8usb import setup, msc_transfer
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)
mbr = bytearray(512)
mbr[0x1BE:0x1CE] = bytes([0, 0, 0, 0, 0x0C, 0, 0, 0]) + struct.pack('<II', 8192, 65536 - 8192)
mbr[510:512] = b'\x55\xAA'; mbr[0:16] = b'R8 TEST CARD MBR'
BLOCKS = 65536

sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(HERE, '..', 'cbuild', 'forth', 'out', 'forth.out'))
B = r8board.Board(sim, clock=False, time_scale=50); U = r8usb.Musb(sim, B, vbus_sensed=False)
C = r8sd.SdModel(sim, blocks=BLOCKS, hc=True, image={0: bytes(mbr)})
sim.mocks[syms['delay_us']] = lambda s: None
res = {}; R = {}
def rd10(lba, n): return [0x28, 0, *struct.pack('>I', lba), 0, *struct.pack('>H', n), 0]
def wr10(lba, n): return [0x2A, 0, *struct.pack('>I', lba), 0, *struct.pack('>H', n), 0]
def serial(m, text, until, timeout=80_000_000):
    n0 = len(m.in_data); m.out_q += text.encode()
    ok = yield m.until(lambda: until.encode() in m.in_data[n0:], f'serial {text!r}', timeout)
    return m.in_data[n0:].decode('latin1')
def reenumerate(m, line, tag):
    c0 = m.connected_at
    m.out_q += line.encode()
    yield m.until(lambda: m.connected_at is not None and m.connected_at != c0, f'reconnect after {line!r}', 100_000_000)
    m.bulk_on = False; m.msc_in.clear(); m.msc_out.clear(); m.out_q.clear()
    r = {}; R['enum_' + tag] = r
    yield from r8usb.mac_enumeration(r)(m)
def script(m):
    yield from r8usb.mac_enumeration(res)(m)
    yield m.until(lambda: b'running word' in m.in_data, 'banner')
    R['banner'] = m.in_data.decode('latin1')
    t = [100]
    def cmd(name, xfer, dir_in, cb, out=b''):
        t[0] += 1
        R[name] = yield from msc_transfer(m, t[0], xfer, dir_in, cb, out)
        R[name + '_cyc'] = m.sim.cycle
    # storage is off at start: no medium. MSC-RO turns it on; the first command then reports UNIT ATTENTION
    yield from cmd('tur_off', 0, False, [0, 0, 0, 0, 0, 0])
    yield from cmd('sense_off', 18, True, [0x03, 0, 0, 0, 18, 0])
    yield from reenumerate(m, 'MSC-RO\r', 'on')
    yield from cmd('tur_ua', 0, False, [0, 0, 0, 0, 0, 0])
    yield from cmd('sense_ua', 18, True, [0x03, 0, 0, 0, 18, 0])
    yield from cmd('inq', 36, True, [0x12, 0, 0, 0, 36, 0])
    yield from cmd('tur', 0, False, [0, 0, 0, 0, 0, 0])
    yield from cmd('cap', 8, True, [0x25] + [0] * 9)
    yield from cmd('ms6', 192, True, [0x1A, 0, 0x3F, 0, 192, 0])
    yield from cmd('ms10', 8, True, [0x5A, 0, 0x3F, 0, 0, 0, 0, 0, 8, 0])
    yield from cmd('rfc', 252, True, [0x23, 0, 0, 0, 0, 0, 0, 0, 252, 0])
    yield from cmd('mbr', 512, True, rd10(0, 1))
    R['off_reading'] = yield from serial(m, 'MSC-OFF\r', ' ok\r\n')    # read but no PREVENT yet (mid-mount): refused
    c0 = m.sim.cycle
    yield from cmd('big', 64 * 512, True, rd10(1000, 64))
    R['big_cycles'] = m.sim.cycle - c0
    yield from cmd('last', 512, True, rd10(BLOCKS - 1, 1))
    R['last_expect'] = C.block(BLOCKS - 1)
    yield from cmd('past', 1024, True, rd10(BLOCKS - 1, 2))
    yield from cmd('sense_past', 18, True, [0x03, 0, 0, 0, 18, 0])
    yield from cmd('write', 512, False, wr10(0, 1), b'\xEE' * 512)
    yield from cmd('sense_wr', 18, True, [0x03, 0, 0, 0, 18, 0])
    yield from cmd('unk', 32, True, [0x9E, 0x10] + [0] * 8 + [0, 0, 0, 32, 0, 0])
    yield from cmd('sense_unk', 18, True, [0x03, 0, 0, 0, 18, 0])
    yield from cmd('prevent', 0, False, [0x1E, 0, 0, 0, 1, 0])
    R['off_inuse'] = yield from serial(m, 'MSC-OFF\r', ' ok\r\n')        # mounted (PREVENT): refused
    yield from cmd('allow', 0, False, [0x1E, 0, 0, 0, 0, 0])
    R['forth'] = yield from serial(m, 'SD-INFO 1 2 + .\r', ' ok\r\n')
    # invalid CBW (30 bytes): both endpoints stall until a Bulk-Only Mass Storage Reset
    m.msc_out.append(r8usb.cbw(999, 0, False, [0] * 6)[:30])
    ok = yield m.until(lambda: m.ep[4].txstall and m.ep[4].rxstall, 'stall after invalid CBW')
    R['stalled'] = bool(m.ep[4].txstall and m.ep[4].rxstall)
    for name, s in (('botreset', setup(0x21, 0xFF, 0, 2, 0)), ('clr_in', setup(0x02, 1, 0, 0x84, 0)), ('clr_out', setup(0x02, 1, 0, 0x04, 0))):
        r = {}; yield from m.control(s, None, r); R[name] = r.get('status'); yield m.delay(5000)
    R['unstalled'] = not m.ep[4].txstall and not m.ep[4].rxstall
    yield from cmd('tur2', 0, False, [0, 0, 0, 0, 0, 0])
    yield from cmd('mbr2', 512, True, rd10(0, 1))
    R['log_ro'] = list(C.log)
    # ---- step 4: writes. MSC-RW drops off the bus; the host enumerates again and sees the WP bit clear
    yield from reenumerate(m, 'MSC-RW\r', 'rw')
    yield from cmd('ms6_rw', 192, True, [0x1A, 0, 0x3F, 0, 192, 0])
    pat1 = bytes((i * 7 + 3) & 255 for i in range(512))
    yield from cmd('w1', 512, False, wr10(2000, 1), pat1)
    yield from cmd('r1', 512, True, rd10(2000, 1))
    R['pat1'] = pat1
    big = bytes(((i * 31) ^ (i >> 9)) & 255 for i in range(300 * 512))
    n0 = len(C.writes)
    yield from cmd('w300', 300 * 512, False, wr10(3000, 300), big)
    R['w300_writes'] = C.writes[n0:]
    yield from cmd('r300', 300 * 512, True, rd10(3000, 300))
    R['bigpat'] = big
    yield from cmd('sync', 0, False, [0x35] + [0] * 9)
    yield from cmd('wlast', 512, False, wr10(BLOCKS - 1, 1), pat1)
    yield from cmd('wpast', 1024, False, wr10(BLOCKS - 1, 2), pat1 * 2)
    yield from cmd('sense_wpast', 18, True, [0x03, 0, 0, 0, 18, 0])
    C.fail_write_at = 5130; n0 = len(C.writes)
    yield from cmd('wfail', 200 * 512, False, wr10(5000, 200), big[:200 * 512])
    C.fail_write_at = None; R['wfail_writes'] = C.writes[n0:]
    yield from cmd('sense_wfail', 18, True, [0x03, 0, 0, 0, 18, 0])
    yield from cmd('tur3', 0, False, [0, 0, 0, 0, 0, 0])
    yield from cmd('w2', 512, False, wr10(2001, 1), pat1[::-1])
    R['forth_w'] = yield from serial(m, '1 BLK 7000 SWAP SD-WRITE .\r', ' ok\r\n')
    n0 = len(C.writes)
    R['sdtest'] = yield from serial(m, '6000 100 7 SD-TEST .\r', ' ok\r\n', 400_000_000)
    R['sdtest_writes'] = C.writes[n0:]
    R['sdtest_gap'] = yield from serial(m, '8100 100 7 SD-TEST . 0 1 7 SD-TEST .\r', ' ok\r\n')
    yield from reenumerate(m, 'MSC-RO\r', 'ro')
    yield from cmd('tur_ro', 0, False, [0, 0, 0, 0, 0, 0])              # consumes the UNIT ATTENTION
    yield from cmd('sense_ro', 18, True, [0x03, 0, 0, 0, 18, 0])
    yield from cmd('eject', 0, False, [0x1B, 0, 0, 0, 2, 0])             # START STOP UNIT, LoEj: storage off
    yield from cmd('prevent2', 0, False, [0x1E, 0, 0, 0, 1, 0])          # macOS re-sends PREVENT: ignored
    yield from cmd('tur_ej', 0, False, [0, 0, 0, 0, 0, 0])
    yield from cmd('sense_ej', 18, True, [0x03, 0, 0, 0, 18, 0])
    R['off_after'] = yield from serial(m, 'MSC-OFF\r', ' ok\r\n')
    yield from cmd('ms6_ro', 192, True, [0x1A, 0, 0x3F, 0, 192, 0])
    yield from cmd('w_ro', 512, False, wr10(2002, 1), pat1)
    R['done'] = True

U.start(script)
class Stop(Exception): pass
def tr(s, pc, pk):
    U.tick()
    if R.get('done') or (U.host is None and res.get('done')): raise Stop
sim.trace = tr
for r in range(64): sim.R[r] = 0
sim.R[S.ridx(1, 3)] = 0xcafe0000
try: sim.run(syms['r8_boot'], 0xcafe0000, max_cycles=3_000_000_000)
except Stop: pass

def ok(name, residue=0, status=0):
    d, c = R.get(name, (b'', None))
    return c is not None and c['status'] == status and c['residue'] == residue
print('  reached:', [k for k in R if not k.endswith('_cyc')][-6:], ' host alive:', U.host is not None)
print('  enumeration:', {k: v.get('status') for k, v in res.items() if isinstance(v, dict)})
print('  host errors:', U.errors, ' card errors:', C.errors, ' stalls:', U.msc_stalls)
cfg = res.get('cfg', {}).get('data', b''); dev = res.get('dev', {}).get('data', b'')
check('device descriptor: composite with IAD (EF/02/01), bcdDevice 2.00', dev[4:7] == bytes([0xEF, 2, 1]) and dev[12:14] == bytes([0, 2]))
check('configuration: 98 bytes, 3 interfaces, IAD for 0-1, MSC interface 08/06/50 with EP 84 + 04, CDC EPs kept',
      len(cfg) == 98 and cfg[4] == 3 and cfg[9:13] == bytes([8, 0x0B, 0, 2]) and bytes([9, 4, 2, 0, 2, 8, 6, 0x50]) in cfg
      and bytes([7, 5, 0x84, 2, 64, 0]) in cfg and bytes([7, 5, 0x04, 2, 64, 0]) in cfg and bytes([7, 5, 0x81, 2, 64, 0]) in cfg)
check('GET_MAX_LUN = 0 (one LUN)', res.get('maxlun', {}).get('data') == b'\x00')
check('serial port still enumerates and opens (banner)', 'running word' in R.get('banner', ''))
d, c = R.get('inq', (b'', None))
check('INQUIRY: 36 bytes, removable, "R8 hack" "R8 SD card"', ok('inq') and len(d) == 36 and d[1] == 0x80 and d[8:15] == b'R8 hack' and d[16:26] == b'R8 SD card')
d = R.get('sense_off', (b'', None))[0]
check('storage off at start: TEST UNIT READY fails, sense NOT READY / MEDIUM NOT PRESENT', ok('tur_off', 0, 1) and len(d) >= 13 and d[2] == 2 and d[12] == 0x3A)
d = R.get('sense_ua', (b'', None))[0]
check('MSC-RO turns it on: first command UNIT ATTENTION (medium may have changed)', R.get('enum_on', {}).get('done') and ok('tur_ua', 0, 1) and len(d) >= 13 and d[2] == 6 and d[12] == 0x28)
check('MSC-OFF refused while the host has it mounted (PREVENT)', 'eject the card on the computer first' in R.get('off_inuse', ''))
check('MSC-OFF refused once the host has read the card, even before PREVENT (mid-mount)', 'eject the card on the computer first' in R.get('off_reading', ''))
d = R.get('sense_ej', (b'', None))[0]
check('host eject turns storage off, and the PREVENT after it is ignored', ok('tur_ej', 0, 1) and len(d) >= 13 and d[2] == 2 and d[12] == 0x3A
      and 'storage off' in R.get('off_after', ''))
check('TEST UNIT READY: good (card initialised on demand)', ok('tur') and C.width == 4)
d, c = R.get('cap', (b'', None))
check('READ CAPACITY: last block 65535, 512 bytes', ok('cap') and struct.unpack('>II', d) == (BLOCKS - 1, 512))
d, c = R.get('ms6', (b'', None))
check('MODE SENSE(6): write-protected header, short packet, residue 188', ok('ms6', 188) and d == bytes([3, 0, 0x80, 0]))
d, c = R.get('ms10', (b'', None))
check('MODE SENSE(10): write-protected', ok('ms10') and len(d) == 8 and d[3] == 0x80)
d, c = R.get('rfc', (b'', None))
check('READ FORMAT CAPACITIES: 65536 blocks of 512', ok('rfc', 240) and len(d) == 12 and struct.unpack('>I', d[4:8])[0] == BLOCKS and d[8] == 2)
check('READ(10) block 0 = the card\'s MBR', ok('mbr') and R['mbr'][0] == bytes(mbr))
d, c = R.get('big', (b'', None))
check('READ(10) 64 blocks from 1000 = card data, 512 packets of 64', ok('big') and d == b''.join(C.block(1000 + i) for i in range(64)))
cyc = R.get('big_cycles', 0)
print(f'  32 KB read: {cyc:,} CPU cycles = {cyc / 360e6 * 1e3:.2f} ms at 360 MHz (card and bus modelled as instant)')
check('READ(10) of the last block', ok('last') and R['last'][0] == R.get('last_expect'))
d, c = R.get('past', (b'', None))
check('READ(10) past the end: failed, 1024 zero bytes (padded), residue 1024', ok('past', 1024, 1) and d == bytes(1024))
d, c = R.get('sense_past', (b'', None))
check('REQUEST SENSE after it: ILLEGAL REQUEST / LBA OUT OF RANGE (05/21)', ok('sense_past') and d[0] == 0x70 and d[2] == 5 and d[12] == 0x21)
check('WRITE(10): data accepted and dropped, failed, residue 512', ok('write', 512, 1))
d, c = R.get('sense_wr', (b'', None))
check('REQUEST SENSE: DATA PROTECT / WRITE PROTECTED (07/27)', ok('sense_wr') and d[2] == 7 and d[12] == 0x27)
check('read-only: no write command reached the card', all(cmd in ('CMD0', 'CMD8', 'CMD55', 'ACMD41', 'CMD2', 'CMD3', 'CMD9', 'CMD7', 'ACMD6', 'CMD17') for cmd, a in R.get('log_ro', ['x'])))
d, c = R.get('unk', (b'', None))
check('unknown command (READ CAPACITY 16): failed, padded, sense 05/20', ok('unk', 32, 1) and d == bytes(32) and R['sense_unk'][0][2] == 5 and R['sense_unk'][0][12] == 0x20)
check('PREVENT ALLOW MEDIUM REMOVAL accepted', ok('prevent'))
f = R.get('forth', ''); print('  forth:', repr(f[-160:]))
check('Forth alongside: SD-INFO and 1 2 + . on the serial port', '65536' in f and '3  ok' in f)
check('invalid CBW: both endpoints stall', R.get('stalled'))
check('reset recovery: BOT reset + CLEAR_FEATURE both endpoints, then commands work',
      R.get('botreset') == 'ok' and R.get('clr_in') == 'ok' and R.get('unstalled') and ok('tur2') and ok('mbr2') and R['mbr2'][0] == bytes(mbr))
print('  --- writes')
check('MSC-RW: device re-enumerates, MODE SENSE now writable (WP clear)', R.get('enum_rw', {}).get('done') and ok('ms6_rw', 188) and R['ms6_rw'][0][2] == 0)
check('WRITE(10) one block: good, card has the data, READ(10) returns it', ok('w1') and C.image.get(2000) == R.get('pat1') and ok('r1') and R['r1'][0] == R['pat1'])
print('  300-block write as card commands:', R.get('w300_writes', [])[:4], '...', len(R.get('w300_writes', [])))
def runs_ok(w, start, n):
    pos = start
    for b, c in w:
        if b != pos or not 1 <= c <= 128: return False
        pos += c
    return pos == start + n
check('WRITE(10) 300 blocks: CMD25 transfers of at most 128 blocks (DMATRIG), card has it all, read back equal',
      ok('w300') and runs_ok(R.get('w300_writes', []), 3000, 300)
      and all(C.image.get(3000 + i) == R['bigpat'][512 * i:512 * i + 512] for i in range(300)) and ok('r300') and R['r300'][0] == R['bigpat'])
check('SYNCHRONIZE CACHE good', ok('sync'))
check('WRITE(10) of the last block good; past the end refused (05/21), data dropped, residue 1024',
      ok('wlast') and ok('wpast', 1024, 1) and R['sense_wpast'][0][2] == 5 and R['sense_wpast'][0][12] == 0x21)
print('  failing write as card commands:', R.get('wfail_writes'))
check('card write error mid-command: failed, residue = data not written (first chunk counted), sense 03/0C',
      ok('wfail', 200 * 512 - 128 * 512, 1) and R['sense_wfail'][0][2] == 3 and R['sense_wfail'][0][12] == 0x0C and runs_ok(R.get('wfail_writes', [])[:-1], 5000, 128) and R['wfail_writes'][-1][0] == 5128)
check('after the error: TEST UNIT READY and a new write work', ok('tur3') and ok('w2') and C.image.get(2001) == R['pat1'][::-1])
check('Forth SD-WRITE: block 1 copied to block 7000', R.get('forth_w', '').endswith('0  ok\r\n') and C.image.get(7000) == C.block(1))
check('Forth SD-TEST: 100 blocks written in runs of <= 128 and read back equal', R.get('sdtest', '').endswith('-1  ok\r\n') and runs_ok(R.get('sdtest_writes', []), 6000, 100))
print('  gap:', repr(R.get('sdtest_gap', '')[-40:]))
check('Forth SD-TEST refuses blocks outside the gap', R.get('sdtest_gap', '').endswith('-2 -2  ok\r\n') and 8100 not in C.image)
check('MSC-RO: re-enumerates write-protected, writes refused again', R.get('enum_ro', {}).get('done') and R['ms6_ro'][0][2] == 0x80 and ok('w_ro', 512, 1) and 2002 not in C.image)
check('no host-side protocol errors, no card protocol errors', not U.errors and not C.errors)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
