#!/usr/bin/env python3
"""Reverse-a-WAV test for Zoom R8 firmware v1.17 (proves the on-device file pipeline for the SPEED feature).

Patch: VERSION menu action slot 0xC06E24EC (0xC07ED7CC -> rev_hook). Everything else stock.
Opening VERSION runs rev_hook, then the normal VERSION screen:
  1. open A:\\REVIN.WAV (mode 0). Missing -> status 1.
  2. require RIFF/WAVE; walk the chunk headers through the file with seek + 24-byte reads (chunks of any size;
     Zoom's recordings carry a ~384 KB LIST chunk before the audio) to find 'fmt ' (block align at +12 of its
     body) and 'data'. Problems -> status 2/3 (6 on I/O error). (Status 4 was dropped: R8 files are whole frames.)
  3. create A:\\REVOUT.WAV (0x101, 0x80); copy every byte before the audio unchanged, in blocks.
  4. from the end of the data backwards, in blocks of CH = 32760 bytes (a multiple of 1..6 and 8, so whole frames):
     seek(fin, pos, 2); read; reverse the order of frames in the block; write.
  5. close both; write A:\\REVSTAT.BIN = 'R8RV', status, data offset, data length, block align, blocks written.
Buffers: 0xC0050000 (block), 0xC0060000 (chunk header), 0xC0061000 (variables); idle outside the updater.
File API (stock): open(&fd, path, mode, attr) 0xC0763C6C, read(fd, buf, len, &got) 0xC0763B2C,
write(fd, buf, len, &n) 0xC0762DB8, seek(fd, off, 2 = from start) 0xC0764050, close(fd) 0xC0765C6C; 0 = OK.

Usage: python3 revtest.py STOCK_R8SYS.BIN OUT/R8SYS.BIN
"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import asm6x, r8fw
from faderbar import file_map, STOCK_SHA

OPEN, READ, WRITE, SEEK, CLOSE = 0xc0763c6c, 0xc0763b2c, 0xc0762db8, 0xc0764050, 0xc0765c6c
ORIG = 0xc07ed7cc
HOOK_SLOT = 0xc06e24ec
CAVE, CAVE_END = 0xc06d9800, 0xc06d9de0
BUF, HDR = 0xc0050000, 0xc0060000
VARBASE = 0xc0061000          # variables live in the idle buffer RAM, not the cave
CH = 32760
RET_B3 = 0x008ca362
# status-file record is VARS[MAGIC..CHUNKS] written as-is: 'R8RV', status, data offset, data length, block align, blocks
VARS = ['MAGIC', 'STATUS', 'DOFF', 'DLEN', 'BA', 'CHUNKS', 'FIN', 'FOUT', 'FST', 'GOT', 'P', 'SZ', 'FMT', 'REM',
        'POS', 'N', 'REV']


def build():
    a = asm6x.Asm(CAVE)
    ret = lambda: (a.raw(RET_B3), a.nop(5))
    refs = []
    def k(v, r):
        if -0x8000 <= v < 0x8000: a.mvkl(v, r)
        else: a.mvkl(v, r); a.mvkh(v, r)
    def ref(name, r):
        refs.append((len(a.items), name)); a.mvkl(0, r); a.mvkh(0, r)
    V = {n: 4 * i for i, n in enumerate(VARS)}
    def getv(name, r, name2=None, r2=None):
        a.ldw('A10', V[name], r)
        if name2: a.ldw('A10', V[name2], r2)
        a.nop(4)
    def setv(name, r): a.stw(r, 'A10', V[name])
    def setc(name, v): k(v, 'A0'); setv(name, 'A0')
    def fail(code): k(code, 'A0'); a.bnop('fail')
    def fail_if(pred, code): a.mvk_p(code, 'A0', pred); a.bnop('fail', pred)
    def nz_fail(reg, code):
        a.l('add_cst', 0, reg, 'A1'); fail_if('A1', code)
    def code_fail():                       # A4 = error code from a subroutine (0 = OK)
        a.l('add_cst', 0, 'A4', 'A0'); a.l('add_cst', 0, 'A4', 'A1'); a.bnop('fail', 'A1')
    def addr_of(name, r):                  # r = &var
        a.l('add_cst', 0, 'A10', r); a.addk(V[name], r)
    def sub_err(code, lab):                # in a subroutine: if A1 then A4 = code, goto lab
        a.mvk_p(code, 'A4', 'A1'); a.bnop(lab, 'A1')

    a.label('rev_hook')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A4', 'B15', 8); a.stw('B4', 'B15', 12); a.stw('A6', 'B15', 16)
    a.stw('B6', 'B15', 20); a.stw('A10', 'B15', 24)
    k(VARBASE, 'A10')
    k(0x56523852, 'A0'); setv('MAGIC', 'A0')                          # 'R8RV'
    k(0, 'A0')
    for n in ('STATUS', 'CHUNKS', 'FIN', 'FOUT', 'FMT', 'BA', 'DOFF', 'DLEN', 'P'): setv(n, 'A0')
    # 1. open input
    addr_of('FIN', 'A4'); ref('PATH_IN', 'B4'); k(0, 'A6'); k(0, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('opened', '!A1')
    setc('FIN', 0); fail(1)
    a.label('opened')
    # 2. RIFF/WAVE, then walk chunk headers through the file (any chunk size)
    a.callp('rdhdr'); code_fail()
    k(HDR + 8, 'A4'); a.callp('le4'); k(0x45564157, 'A5'); a.l('sub', 'A4', 'A5', 'A2'); nz_fail('A2', 2)   # 'WAVE'
    setc('P', 12)
    a.label('chunk')
    a.callp('rdhdr'); code_fail()
    getv('GOT', 'A0'); k(8, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); fail_if('A1', 3)
    k(HDR + 4, 'A4'); a.callp('le4'); setv('SZ', 'A4')
    k(HDR, 'A4'); a.callp('le4')
    k(0x61746164, 'A5'); a.l('sub', 'A4', 'A5', 'A2')                   # 'data'
    a.bnop('not_data', 'A2')
    getv('P', 'A0'); a.l('add_cst', 8, 'A0', 'A0'); setv('DOFF', 'A0')
    getv('SZ', 'A0'); setv('DLEN', 'A0')
    a.bnop('found')
    a.label('not_data')
    k(0x20746d66, 'A5'); a.l('sub', 'A4', 'A5', 'A2')                   # 'fmt '
    a.bnop('not_fmt', 'A2')
    getv('GOT', 'A0'); k(24, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); fail_if('A1', 3)
    k(HDR + 8 + 12, 'A4'); k(2, 'B4'); a.callp('le'); setv('BA', 'A4')
    setc('FMT', 1)
    a.label('not_fmt')
    getv('P', 'A7', 'SZ', 'A5')
    a.l('add', 'A7', 'A5', 'A0'); a.l('add_cst', 8, 'A0', 'A0')
    k(1, 'A9'); a.l('and', 'A5', 'A9', 'A5'); a.l('add', 'A0', 'A5', 'A0')   # + pad byte after odd sizes
    a.l('cmpltu', 'A0', 'A7', 'A1'); fail_if('A1', 3)                  # wrapped: corrupt size
    setv('P', 'A0')
    a.bnop('chunk')

    a.label('found')
    getv('FMT', 'A1'); fail_if('!A1', 3)
    getv('BA', 'A0'); a.l('add_cst', -1, 'A0', 'A0'); k(8, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1')   # BA 1..8
    fail_if('!A1', 3)
    # 3. output; copy everything before the audio unchanged
    addr_of('FOUT', 'A4'); ref('PATH_OUT', 'B4'); k(0x101, 'A6'); k(0x80, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('out_ok', '!A1')
    setc('FOUT', 0); fail(5)
    a.label('out_ok')
    getv('DOFF', 'A0'); setv('REM', 'A0'); setc('POS', 0); setc('REV', 0)
    a.label('hcopy')
    a.callp('next_n'); a.l('add_cst', 0, 'A0', 'A1'); a.bnop('hdone', '!A1')
    a.callp('xfer'); code_fail()
    getv('POS', 'A0', 'N', 'A5'); a.l('add', 'A0', 'A5', 'A0'); setv('POS', 'A0')
    a.callp('rem_n')
    a.bnop('hcopy')
    a.label('hdone')
    # 4. audio blocks, last first, frames reversed
    getv('DLEN', 'A0', 'DOFF', 'A5'); setv('REM', 'A0'); a.l('add', 'A0', 'A5', 'A0'); setv('POS', 'A0')
    setc('REV', 1)
    a.label('blk')
    a.callp('next_n'); a.l('add_cst', 0, 'A0', 'A1'); a.bnop('finish', '!A1')
    getv('POS', 'A5'); a.l('sub', 'A5', 'A0', 'A5'); setv('POS', 'A5')
    a.callp('xfer'); code_fail()
    a.callp('rem_n')
    getv('CHUNKS', 'A0'); a.l('add_cst', 1, 'A0', 'A0'); setv('CHUNKS', 'A0')
    a.bnop('blk')

    a.label('fail')
    setv('STATUS', 'A0')
    # 5. close, status file, normal VERSION screen
    a.label('finish')
    for fd in ('FOUT', 'FIN'):
        getv(fd, 'A4'); a.l('add_cst', 0, 'A4', 'A1'); a.bnop(f'no_{fd}', '!A1')
        a.callp(CLOSE); a.label(f'no_{fd}')
    addr_of('FST', 'A4'); ref('PATH_STAT', 'B4'); k(0x101, 'A6'); k(0x80, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('done', 'A1')
    getv('FST', 'A4'); a.l('add_cst', 0, 'A10', 'B4'); k(24, 'A6'); addr_of('GOT', 'B6')
    a.callp(WRITE)
    getv('FST', 'A4'); a.callp(CLOSE)
    a.label('done')
    a.ldw('B15', 8, 'A4'); a.ldw('B15', 12, 'B4'); a.ldw('B15', 16, 'A6'); a.ldw('B15', 20, 'B6')
    a.ldw('B15', 24, 'A10'); a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(32, 'B15')
    a.b(ORIG); a.nop(5)

    # next_n: N = min(REM, CH) -> A0 (0 when finished). Leaf.
    a.label('next_n')
    getv('REM', 'A0'); k(CH, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); a.bnop('nn_rem', 'A1')
    a.l('add_cst', 0, 'A5', 'A0')
    a.label('nn_rem')
    setv('N', 'A0'); ret()
    # rem_n: REM -= N. Leaf.
    a.label('rem_n')
    getv('REM', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A0'); setv('REM', 'A0'); ret()

    # rdhdr: seek(FIN, P); read 24 bytes to HDR -> A4 = 0 or 6, GOT = bytes read
    a.label('rdhdr')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
    getv('FIN', 'A4', 'P', 'B4'); k(2, 'A6'); a.callp(SEEK)
    a.l('add_cst', 0, 'A4', 'A1'); sub_err(6, 'rh_ret')
    getv('FIN', 'A4'); k(HDR, 'B4'); k(24, 'A6'); addr_of('GOT', 'B6'); a.callp(READ)
    a.l('add_cst', 0, 'A4', 'A1'); sub_err(6, 'rh_ret')
    a.label('rh_ret')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15'); ret()

    # xfer: seek(FIN, POS); read N to BUF; if REV reverse frames; write N to FOUT -> A4 = 0, 6 or 7
    a.label('xfer')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
    getv('FIN', 'A4', 'POS', 'B4'); k(2, 'A6'); a.callp(SEEK)
    a.l('add_cst', 0, 'A4', 'A1'); sub_err(6, 'xf_ret')
    getv('FIN', 'A4', 'N', 'A6'); k(BUF, 'B4'); addr_of('GOT', 'B6'); a.callp(READ)
    a.l('add_cst', 0, 'A4', 'A1'); sub_err(6, 'xf_ret')
    getv('GOT', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A1'); sub_err(6, 'xf_ret')
    getv('REV', 'A1'); a.bnop('xf_write', '!A1')
    k(BUF, 'A7'); getv('N', 'A0', 'BA', 'A9'); a.l('add', 'A7', 'A0', 'A8')
    a.l('sub', 'A8', 'A9', 'A8'); a.l('add', 'A9', 'A9', 'A2')
    a.label('rv_frame')
    a.l('cmpltu', 'A7', 'A8', 'A1'); a.bnop('xf_write', '!A1')
    a.l('add_cst', 0, 'A9', 'B1')
    a.label('rv_byte')
    a.ldst2('ldbu', 'A7', 0, 'A0'); a.ldst2('ldbu', 'A8', 0, 'A5'); a.nop(4)
    a.ldst2('stb', 'A7', 1, 'A5', 'post++'); a.ldst2('stb', 'A8', 1, 'A0', 'post++')
    a.addk(-1, 'B1'); a.bnop('rv_byte', 'B1')
    a.l('sub', 'A8', 'A2', 'A8'); a.bnop('rv_frame')
    a.label('xf_write')
    getv('FOUT', 'A4', 'N', 'A6'); k(BUF, 'B4'); addr_of('GOT', 'B6'); a.callp(WRITE)
    a.l('add_cst', 0, 'A4', 'A1'); sub_err(7, 'xf_ret')
    getv('GOT', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A1'); sub_err(7, 'xf_ret')
    k(0, 'A4')
    a.label('xf_ret')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15'); ret()

    # le(A4 ptr, B4 nbytes) -> A4 little-endian value. Leaf. Clobbers A0 A5 A7 B1
    a.label('le4')
    k(4, 'B4')
    a.label('le')
    a.l('add', 'A4', 'B4', 'A7'); k(0, 'A0'); a.l('add_cst', 0, 'B4', 'B1')
    a.label('le_loop')
    for _ in range(8): a.l('add', 'A0', 'A0', 'A0')
    a.l('add_cst', -1, 'A7', 'A7'); a.ldst2('ldbu', 'A7', 0, 'A5'); a.nop(4)
    a.l('add', 'A0', 'A5', 'A0')
    a.addk(-1, 'B1'); a.bnop('le_loop', 'B1')
    a.l('add_cst', 0, 'A0', 'A4')
    ret()

    code_len = len(a.assemble())
    data = [('PATH_IN', b'A:\\REVIN.WAV\0'), ('PATH_OUT', b'A:\\REVOUT.WAV\0'), ('PATH_STAT', b'A:\\REVSTAT.BIN\0')]
    addr, blob_d, pos = {}, bytearray(), CAVE + code_len
    for name, b in data:
        addr[name] = pos; blob_d += b; pos += len(b)
    for idx, name in refs:
        v = addr[name]
        a.items[idx] = ('mvk', v & 0xffff, a.items[idx][2], 0)
        a.items[idx + 1] = ('mvk', (v >> 16) & 0xffff, a.items[idx + 1][2], 1)
    code = a.assemble()
    assert len(code) == code_len
    blob = code + bytes(blob_d)
    assert CAVE + len(blob) <= CAVE_END, f'too big: {len(blob)} bytes, cave {CAVE_END - CAVE}'
    return blob, a, addr


def patch(stock):
    d = bytearray(stock)
    off = file_map(d)
    blob, a, addr = build()
    o = off(CAVE, len(blob)); d[o:o + len(blob)] = blob
    so = off(HOOK_SLOT, 4)
    assert struct.unpack_from('<I', d, so)[0] == ORIG
    struct.pack_into('<I', d, so, a.labels['rev_hook'])
    struct.pack_into('<I', d, 0x34, r8fw.checksum(d))
    return bytes(d), blob, (o, so)


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    stock = open(src, 'rb').read()
    assert hashlib.sha256(stock).hexdigest().startswith(STOCK_SHA), 'not stock v1.17'
    out, blob, (co, so) = patch(stock)
    diff = [i for i in range(len(out)) if out[i] != stock[i]]
    assert all(co <= i < co + len(blob) or so <= i < so + 4 or 0x34 <= i < 0x38 for i in diff)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    print(f'hook+data {len(blob)} of {CAVE_END - CAVE} cave bytes; {len(diff)} bytes changed (cave, VERSION slot, checksum)')
    print('sha256', hashlib.sha256(out).hexdigest())
