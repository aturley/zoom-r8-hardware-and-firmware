#!/usr/bin/env python3
"""TRACK EDIT menu snapshot + flash-code loader self-test, for Zoom R8 firmware v1.17.

Patches (everything else stock):
  * 0xC072FEB8: CALLP menu_engine (0xC078ED80) inside the TRACK EDIT builder -> CALLP dump_hook (displacement only).
  * Code cave 0xC06D9800: dump_hook + loader.
  * File padding at 0x212700 (flash 0x2926C0, unused 0xFF fill between the boot script and the sample bank):
    a small test program assembled for 0xC0070000.
dump_hook runs once per power-on (flag kept in the cave, zero in the image), just before the TRACK EDIT menu:
  1. A:\\MENUDUMP.BIN = 32-byte header ('R8MD', the menu engine's A4 B4 A6 B6, the TRACK EDIT track index
     word at 0xC0813024, 0xC05E0000, 0xC0812000) + RAM 0xC05E0000..0xC0600000 (menu records, track and file
     tables) + RAM 0xC0812000..0xC0814000 (B14 globals).
  2. Loader self-test: flash_read(0x2926C0, 0xC0070000, len); Cache_wbInv(0xC0070000, len, 1); Cache_invL1pAll();
     call 0xC0070000. The test program calls strlen("REVERSE") through an indirect far call and returns
     0x600DC0D0 + 7. A:\\LOADTEST.BIN = 'R8LT' + that value.
  3. Restores the arguments and continues into the menu engine (the menu appears as normal).
Usage: python3 menudump.py STOCK_R8SYS.BIN OUT/R8SYS.BIN
"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import asm6x, r8fw
from faderbar import file_map, asm6x_sext, STOCK_SHA

OPEN, WRITE, CLOSE = 0xc0763c6c, 0xc0762db8, 0xc0765c6c
FLASH_READ = 0xc07d1550
CACHE_WBINV, CACHE_INV_L1P = 0xc07fc460, 0xc07f8860
STRLEN = 0xc07fb800
MENU_ENGINE = 0xc078ed80
SITE = 0xc072feb8
TRACK_IDX = 0xc0813024                 # B14 + 0x504
CAVE, CAVE_END = 0xc06d9800, 0xc06d9de0
VARS = 0xc0062000                      # idle buffer RAM
CODEBUF = 0xc0070000
BLOB_OFF = 0x212700                    # file offset inside the 0xFF padding (0x2126E8..0x220040)
BLOB_FLASH = 0x80000 + BLOB_OFF - 0x40
DUMPS = [(0xc05e0000, 0x20000), (0xc0812000, 0x2000)]
CHUNK = 0x4000
RET_B3 = 0x008ca362


def b_reg(n):
    return (0x008ca362 & ~(0x1f << 18)) | (n << 18)


class Prog:
    """asm6x wrapper with late-bound address references (data items and labels)."""
    def __init__(self, base):
        self.a = asm6x.Asm(base); self.refs = []
    def k(self, v, r):
        if -0x8000 <= v < 0x8000: self.a.mvkl(v, r)
        else: self.a.mvkl(v, r); self.a.mvkh(v, r)
    def ref(self, name, r):
        self.refs.append((len(self.a.items), name)); self.a.mvkl(0, r); self.a.mvkh(0, r)
    def farcall(self, target, lab):
        self.k(target, 'B5'); self.ref(lab, 'B3'); self.a.raw(b_reg(5)); self.a.nop(5); self.a.label(lab)
    def link(self, data, align=4):
        code_len = len(self.a.assemble())
        addr, blob, pos = {}, bytearray(), self.a.base + code_len
        for name, b in data:
            pad = (-pos) % align; blob += bytes(pad); pos += pad
            addr[name] = pos; blob += b; pos += len(b)
        for idx, name in self.refs:
            v = addr[name] if name in addr else self.a.labels[name]
            it = self.a.items
            it[idx] = ('mvk', v & 0xffff, it[idx][2], 0); it[idx + 1] = ('mvk', (v >> 16) & 0xffff, it[idx + 1][2], 1)
        code = self.a.assemble()
        assert len(code) == code_len
        return code + bytes(blob), addr


def build_blob():
    p = Prog(CODEBUF); a = p.a
    a.label('blob_entry')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
    p.ref('STR', 'A4')
    p.farcall(STRLEN, 'r_strlen')
    p.k(0x600DC0D0, 'A5'); a.l('add', 'A4', 'A5', 'A4')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15')
    a.raw(RET_B3); a.nop(5)
    return p.link([('STR', b'REVERSE\0')])


def build_cave(blob_len):
    p = Prog(CAVE); a = p.a; k = p.k
    # FLAG lives in the cave (zero in the image): the idle RAM at VARS is NOT zero at power-on (boot loader buffers).
    V = {'FD': 4, 'W': 8, 'HDR': 16, 'RES': 48, 'PTR': 56, 'LEFT': 60}   # HDR = 32 bytes, RES = 8
    def getv(n, r): a.ldw('A10', V[n], r); a.nop(4)
    def setv(n, r): a.stw(r, 'A10', V[n])
    def addr_of(n, r): a.l('add_cst', 0, 'A10', r); a.addk(V[n], r)

    a.label('dump_hook')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A4', 'B15', 8); a.stw('B4', 'B15', 12); a.stw('A6', 'B15', 16)
    a.stw('B6', 'B15', 20); a.stw('A10', 'B15', 24)
    k(VARS, 'A10')
    p.ref('FLAG', 'A3'); a.ldw('A3', 0, 'A1'); a.nop(4); a.bnop('done', 'A1')
    k(1, 'A0'); a.stw('A0', 'A3', 0)
    # header
    k(0x444d3852, 'A0'); a.stw('A0', 'A10', V['HDR'])                    # 'R8MD'
    for i, off in enumerate((8, 12, 16, 20)):
        a.ldw('B15', off, 'A0'); a.nop(4); a.stw('A0', 'A10', V['HDR'] + 4 + 4 * i)
    k(TRACK_IDX, 'A3'); a.ldw('A3', 0, 'A0'); a.nop(4); a.stw('A0', 'A10', V['HDR'] + 20)
    for i, (base, _) in enumerate(DUMPS):
        k(base, 'A0'); a.stw('A0', 'A10', V['HDR'] + 24 + 4 * i)
    # 1. dump file
    addr_of('FD', 'A4'); p.ref('P_DUMP', 'B4'); k(0x101, 'A6'); k(0x80, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('loadtest', 'A1')
    getv('FD', 'A4'); addr_of('HDR', 'B4'); k(32, 'A6'); addr_of('W', 'B6'); a.callp(WRITE)
    for i, (base, size) in enumerate(DUMPS):
        ch = min(CHUNK, size); assert size % ch == 0 and size // ch >= 1
        k(base, 'A0'); setv('PTR', 'A0'); k(size // ch, 'A0'); setv('LEFT', 'A0')
        a.label(f'dl{i}')
        getv('FD', 'A4'); getv('PTR', 'B4'); k(ch, 'A6'); addr_of('W', 'B6'); a.callp(WRITE)
        getv('PTR', 'A0'); k(ch, 'A5'); a.l('add', 'A0', 'A5', 'A0'); setv('PTR', 'A0')
        getv('LEFT', 'A0'); a.l('add_cst', -1, 'A0', 'A0'); setv('LEFT', 'A0')
        a.l('add_cst', 0, 'A0', 'A1'); a.bnop(f'dl{i}', 'A1')
    getv('FD', 'A4'); a.callp(CLOSE)
    # 2. loader self-test
    a.label('loadtest')
    k(BLOB_FLASH, 'A4'); k(CODEBUF, 'B4'); k(blob_len, 'A6'); a.callp(FLASH_READ)
    k(CODEBUF, 'A4'); k(blob_len, 'B4'); k(1, 'B6'); a.callp(CACHE_WBINV)
    a.callp(CACHE_INV_L1P)
    k(CODEBUF, 'B5'); p.ref('after_blob', 'B3'); a.raw(b_reg(5)); a.nop(5)
    a.label('after_blob')
    a.stw('A4', 'A10', V['RES'] + 4)
    k(0x544c3852, 'A0'); a.stw('A0', 'A10', V['RES'])                    # 'R8LT'
    addr_of('FD', 'A4'); p.ref('P_LT', 'B4'); k(0x101, 'A6'); k(0x80, 'B6')
    a.callp(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('done', 'A1')
    getv('FD', 'A4'); addr_of('RES', 'B4'); k(8, 'A6'); addr_of('W', 'B6'); a.callp(WRITE)
    getv('FD', 'A4'); a.callp(CLOSE)
    # 3. continue into the menu engine with the original arguments and return address
    a.label('done')
    a.ldw('B15', 8, 'A4'); a.ldw('B15', 12, 'B4'); a.ldw('B15', 16, 'A6'); a.ldw('B15', 20, 'B6')
    a.ldw('B15', 24, 'A10'); a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(32, 'B15')
    a.b(MENU_ENGINE); a.nop(5)
    blob, addr = p.link([('FLAG', bytes(4)), ('P_DUMP', b'A:\\MENUDUMP.BIN\0'), ('P_LT', b'A:\\LOADTEST.BIN\0')])
    assert CAVE + len(blob) <= CAVE_END, f'cave too big: {len(blob)}'
    return blob, a


def patch(stock, blob=None):
    d = bytearray(stock)
    off = file_map(d)
    bl = blob if blob is not None else build_blob()[0]
    assert BLOB_OFF + len(bl) <= 0x220040 and all(b == 0xff for b in d[BLOB_OFF:BLOB_OFF + len(bl)])
    d[BLOB_OFF:BLOB_OFF + len(bl)] = bl
    cave, a = build_cave(len(bl))
    o = off(CAVE, len(cave)); d[o:o + len(cave)] = cave
    so = off(SITE, 4)
    w = struct.unpack_from('<I', d, so)[0]
    assert (w >> 28) == 1 and (w >> 2) & 0x1f == 0b00100
    assert ((SITE & ~31) + (asm6x_sext((w >> 7) & 0x1fffff, 21) << 2)) & 0xffffffff == MENU_ENGINE
    disp = (a.labels['dump_hook'] - (SITE & ~31)) // 4
    struct.pack_into('<I', d, so, (w & ~(0x1fffff << 7)) | ((disp & 0x1fffff) << 7))
    struct.pack_into('<I', d, 0x34, r8fw.checksum(d))
    return bytes(d), (o, len(cave)), so, len(bl)


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    stock = open(src, 'rb').read()
    assert hashlib.sha256(stock).hexdigest().startswith(STOCK_SHA), 'not stock v1.17'
    out, (co, cl), so, bl = patch(stock)
    diff = [i for i in range(len(out)) if out[i] != stock[i]]
    assert all(co <= i < co + cl or so <= i < so + 4 or 0x34 <= i < 0x38 or BLOB_OFF <= i < BLOB_OFF + bl for i in diff)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    print(f'cave {cl} bytes, flash-loaded test program {bl} bytes at file {BLOB_OFF:#x} (flash {BLOB_FLASH:#x}); '
          f'{len(diff)} bytes changed')
    print('sha256', hashlib.sha256(out).hexdigest())
