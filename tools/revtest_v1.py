#!/usr/bin/env python3
"""Reverse-a-WAV test for Zoom R8 firmware v1.17 (proves the on-device file pipeline for the SPEED feature).

Patch: VERSION menu action slot 0xC06E24EC (0xC07ED7CC -> rev_hook). Everything else stock.
Opening VERSION runs rev_hook, then the normal VERSION screen:
  1. open A:\\REVIN.WAV (mode 0). Missing -> status 1.
  2. read up to 4 KB of header; require RIFF/WAVE; walk chunks to find 'fmt ' (block align at +12 of its body)
     and 'data' (offset + length). Problems -> status 2/3. Data length must be a whole number of frames -> else 4.
  3. create A:\\REVOUT.WAV (0x101, 0x80); write every header byte up to the start of the audio unchanged.
  4. from the end of the data backwards, in blocks of CH = 32760 bytes (a multiple of 1..6 and 8, so whole frames):
     seek(fin, pos, 2); read; reverse the order of frames in the block; write.
  5. close both; write A:\\REVSTAT.BIN = 'R8RV', status, data offset, data length, block align, blocks written.
Buffers: 0xC0050000 (block) and 0xC0060000 (header), idle outside the boot updater.
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
HDR_MAX = 4096
CH = 32760
RET_B3 = 0x008ca362
# status-file record is VARS[MAGIC..CHUNKS] written as-is: 'R8RV', status, data offset, data length, block align, blocks
VARS = ['MAGIC', 'STATUS', 'DOFF', 'DLEN', 'BA', 'CHUNKS', 'FIN', 'FOUT', 'FST', 'GOT', 'HN', 'P', 'SZ', 'FMT', 'REM', 'POS', 'N']


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
    def nz_fail(reg, code, lab):
        """if reg != 0: fail(code)"""
        a.l('add_cst', 0, reg, 'A1'); a.mvk_p(code, 'A0', 'A1'); a.bnop('fail', 'A1')

    a.label('rev_hook')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A4', 'B15', 8); a.stw('B4', 'B15', 12); a.stw('A6', 'B15', 16)
    a.stw('B6', 'B15', 20); a.stw('A10', 'B15', 24)
    ref('VARS', 'A10')
    k(0, 'A0')
    for n in ('STATUS', 'CHUNKS', 'FIN', 'FOUT', 'FMT', 'BA', 'DOFF', 'DLEN'): setv(n, 'A0')
    # 1. open input
    a.l('add_cst', 0, 'A10', 'A4'); a.addk(V['FIN'], 'A4'); ref('PATH_IN', 'B4'); k(0, 'A6'); k(0, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('opened', '!A1')
    setc('FIN', 0); fail(1)
    a.label('opened')
    # 2. header
    getv('FIN', 'A4'); k(HDR, 'B4'); k(HDR_MAX, 'A6'); a.l('add_cst', 0, 'A10', 'B6'); a.addk(V['GOT'], 'B6')
    a.callp(READ)
    nz_fail('A4', 2, 'hdr_ok')
    getv('GOT', 'A0'); setv('HN', 'A0')
    for off, tag, lab in ((0, 0x46464952, 'riff'), (8, 0x45564157, 'wave')):
        k(HDR + off, 'A4'); k(4, 'B4'); a.callp('le'); k(tag, 'A5'); a.l('sub', 'A4', 'A5', 'A2'); nz_fail('A2', 2, lab)
    setc('P', 12)
    a.label('chunk')
    getv('P', 'A0'); a.l('add_cst', 8, 'A0', 'A0'); getv('HN', 'A5')
    a.l('cmpltu', 'A5', 'A0', 'A1'); a.mvk_p(3, 'A0', 'A1'); a.bnop('fail', 'A1')
    getv('P', 'A0'); k(HDR + 4, 'A4'); a.l('add', 'A4', 'A0', 'A4'); k(4, 'B4'); a.callp('le'); setv('SZ', 'A4')
    getv('P', 'A0'); k(HDR, 'A4'); a.l('add', 'A4', 'A0', 'A4'); k(4, 'B4'); a.callp('le')
    k(0x61746164, 'A5'); a.l('sub', 'A4', 'A5', 'A2')                   # 'data'
    a.bnop('not_data', 'A2')
    getv('P', 'A0'); a.l('add_cst', 8, 'A0', 'A0'); setv('DOFF', 'A0')
    getv('SZ', 'A0'); setv('DLEN', 'A0')
    a.bnop('found')
    a.label('not_data')
    getv('HN', 'A5', 'SZ', 'A0'); a.l('cmpltu', 'A5', 'A0', 'A1')  # other chunks must fit the header read
    a.mvk_p(3, 'A0', 'A1'); a.bnop('fail', 'A1')
    k(0x20746d66, 'A5'); a.l('sub', 'A4', 'A5', 'A2')                   # 'fmt '
    a.bnop('not_fmt', 'A2')
    getv('P', 'A0'); k(HDR + 8 + 12, 'A4'); a.l('add', 'A4', 'A0', 'A4'); k(2, 'B4'); a.callp('le'); setv('BA', 'A4')
    setc('FMT', 1)
    a.label('not_fmt')
    getv('P', 'A0', 'SZ', 'A5'); a.l('add', 'A0', 'A5', 'A0'); a.l('add_cst', 8, 'A0', 'A0')
    k(1, 'A7'); a.l('and', 'A5', 'A7', 'A5'); a.l('add', 'A0', 'A5', 'A0')   # pad byte after odd sizes
    setv('P', 'A0')
    a.bnop('chunk')

    a.label('found')
    getv('FMT', 'A1'); a.mvk_p(3, 'A0', '!A1'); a.bnop('fail', '!A1')
    getv('BA', 'A0'); a.l('add_cst', -1, 'A0', 'A0'); k(8, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1')   # BA-1 < 8
    a.mvk_p(3, 'A0', '!A1'); a.bnop('fail', '!A1')
    getv('DLEN', 'A0'); k(CH, 'A5')
    a.label('mod_ch')
    a.l('cmpltu', 'A0', 'A5', 'A1'); a.bnop('mod_ba', 'A1')
    a.l('sub', 'A0', 'A5', 'A0'); a.bnop('mod_ch')
    a.label('mod_ba')
    getv('BA', 'A5')
    a.label('mod_ba2')
    a.l('cmpltu', 'A0', 'A5', 'A1'); a.bnop('mod_done', 'A1')
    a.l('sub', 'A0', 'A5', 'A0'); a.bnop('mod_ba2')
    a.label('mod_done')
    nz_fail('A0', 4, 'frames_ok')
    # 3. output + header
    a.l('add_cst', 0, 'A10', 'A4'); a.addk(V['FOUT'], 'A4'); ref('PATH_OUT', 'B4'); k(0x101, 'A6'); k(0x80, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('out_ok', '!A1')
    setc('FOUT', 0); fail(5)
    a.label('out_ok')
    getv('FOUT', 'A4'); k(HDR, 'B4'); getv('DOFF', 'A6')
    a.callp('wr_chk')
    # 4. blocks, last first
    getv('DLEN', 'A0'); setv('REM', 'A0')
    getv('DOFF', 'A5'); a.l('add', 'A0', 'A5', 'A0'); setv('POS', 'A0')
    a.label('blk')
    getv('REM', 'A0'); a.l('add_cst', 0, 'A0', 'A1'); a.bnop('finish', '!A1')
    k(CH, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); a.bnop('n_rem', 'A1')
    a.l('add_cst', 0, 'A5', 'A0')
    a.label('n_rem')
    setv('N', 'A0')
    getv('POS', 'A5'); a.l('sub', 'A5', 'A0', 'A5'); setv('POS', 'A5')
    getv('FIN', 'A4', 'POS', 'B4'); k(2, 'A6')
    a.callp(SEEK)
    nz_fail('A4', 6, 'sk_ok')
    getv('FIN', 'A4'); k(BUF, 'B4'); getv('N', 'A6'); a.l('add_cst', 0, 'A10', 'B6'); a.addk(V['GOT'], 'B6')
    a.callp(READ)
    nz_fail('A4', 6, 'rd_ok')
    getv('GOT', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A2'); nz_fail('A2', 6, 'rd_n_ok')
    k(BUF, 'A7'); getv('N', 'A0'); a.l('add', 'A7', 'A0', 'A8')
    getv('BA', 'A9'); a.l('sub', 'A8', 'A9', 'A8'); a.l('add', 'A9', 'A9', 'A2')
    a.label('rv_frame')
    a.l('cmpltu', 'A7', 'A8', 'A1'); a.bnop('rv_done', '!A1')
    a.l('add_cst', 0, 'A9', 'B1')
    a.label('rv_byte')
    a.ldst2('ldbu', 'A7', 0, 'A0'); a.ldst2('ldbu', 'A8', 0, 'A5'); a.nop(4)
    a.ldst2('stb', 'A7', 1, 'A5', 'post++'); a.ldst2('stb', 'A8', 1, 'A0', 'post++')
    a.addk(-1, 'B1'); a.bnop('rv_byte', 'B1')
    a.l('sub', 'A8', 'A2', 'A8'); a.bnop('rv_frame')
    a.label('rv_done')
    getv('FOUT', 'A4'); k(BUF, 'B4'); getv('N', 'A6')
    a.callp('wr_chk')
    getv('REM', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A0'); setv('REM', 'A0')
    getv('CHUNKS', 'A0'); a.l('add_cst', 1, 'A0', 'A0'); setv('CHUNKS', 'A0')
    a.bnop('blk')

    a.label('fail')
    setv('STATUS', 'A0')
    # 5. close, status file, normal VERSION screen
    a.label('finish')
    for fd in ('FOUT', 'FIN'):
        getv(fd, 'A4'); a.l('add_cst', 0, 'A4', 'A1'); a.bnop(f'no_{fd}', '!A1')
        a.callp(CLOSE); a.label(f'no_{fd}')
    a.l('add_cst', 0, 'A10', 'A4'); a.addk(V['FST'], 'A4'); ref('PATH_STAT', 'B4'); k(0x101, 'A6'); k(0x80, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('done', 'A1')
    getv('FST', 'A4'); a.l('add_cst', 0, 'A10', 'B4'); a.addk(V['MAGIC'], 'B4'); k(24, 'A6')
    a.l('add_cst', 0, 'A10', 'B6'); a.addk(V['GOT'], 'B6')
    a.callp(WRITE)
    getv('FST', 'A4'); a.callp(CLOSE)
    a.label('done')
    a.ldw('B15', 8, 'A4'); a.ldw('B15', 12, 'B4'); a.ldw('B15', 16, 'A6'); a.ldw('B15', 20, 'B6')
    a.ldw('B15', 24, 'A10'); a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(32, 'B15')
    a.b(ORIG); a.nop(5)

    # wr_chk(A4 fd, B4 buf, A6 n): write; any error or short write -> status 7 and finish (stack unwound)
    a.label('wr_chk')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4); a.stw('A6', 'B15', 8)
    a.l('add_cst', 0, 'A10', 'B6'); a.addk(V['GOT'], 'B6')
    a.callp(WRITE)
    a.ldw('B15', 8, 'A5'); getv('GOT', 'A0'); a.l('sub', 'A0', 'A5', 'A0'); a.l('add', 'A0', 'A4', 'A2')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15')
    a.bnop('wr_ret', '!A2')
    k(7, 'A0'); a.bnop('fail')                                   # (callee frame already popped)
    a.label('wr_ret')
    ret()

    # le(A4 ptr, B4 nbytes) -> A4 little-endian value. Leaf. Clobbers A0 A5 A7 B1
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
    data = [('PATH_IN', b'A:\\REVIN.WAV\0'), ('PATH_OUT', b'A:\\REVOUT.WAV\0'), ('PATH_STAT', b'A:\\REVSTAT.BIN\0'),
            ('VARS', b''.join(b'R8RV' if n == 'MAGIC' else bytes(4) for n in VARS))]
    addr, blob_d, pos = {}, bytearray(), CAVE + code_len
    for name, b in data:
        pad = (-pos) % 4; blob_d += bytes(pad); pos += pad
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
