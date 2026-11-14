#!/usr/bin/env python3
"""TOOL > SD CARD with RUN APP and RUN UTIL, plus REVERSE in TRACK EDIT, for Zoom R8 firmware v1.17.

Everything appmenu.py does, plus a fifth SD CARD item. The stock SD CARD list (0xC0715700) has only 4 slots and the
next menu's list follows it, so the menu gets a new list in the cave: EXCHANGE, FORMAT, REMAIN, RUN APP, RUN UTIL, 0.
The descriptor's list pointer (+32) is pointed at it and its count set to 5.
RUN UTIL's handler (util_item) is RUN APP's handler with A4 = 1: the launcher's r8_main(1, ..) loads
A:\\R8UTILS\\UTILMENU.SYS (an R8UT file, cbuild/launcher/util.h) into idle DDR, checks it and calls it, and returns
to the menu. Everything about listing and running utilities lives in UTILMENU.SYS, so it changes without a reflash.
Usage: python3 utilmenu.py STOCK_R8SYS.BIN cbuild/launcher/out/launch.out OUT/R8SYS.BIN"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r8fw, appmenu
from faderbar import file_map, asm6x_sext, STOCK_SHA
from menudump import Prog, b_reg, MENU_ENGINE, SITE, CAVE, CAVE_END, CODEBUF, BLOB_OFF, FLASH_READ, CACHE_WBINV, \
    CACHE_INV_L1P, BLOB_FLASH
from revmenu import TRIM_HANDLER, CARD_CHECK, RET_B3
from appmenu import LAUNCH, SD_DESC, SD_LIST, FORMAT_REC, PAD_END, build_blob

def build_cave(blob_len):
    p = Prog(CAVE); a = p.a; k = p.k
    # menu_hook: exactly as appmenu.py (REVERSE in TRACK EDIT)
    a.label('menu_hook')
    a.addk(-16, 'B15'); a.stw('B3', 'B15', 4); a.stw('A4', 'B15', 8)
    a.ldw('A4', 0, 'A0'); a.nop(4)
    k(2, 'A5'); a.l('sub', 'A0', 'A5', 'A1'); a.bnop('mh_done', 'A1')
    a.ldw('A4', 32, 'A7'); a.nop(4)
    a.ldw('A7', 4, 'A8'); a.nop(4)
    a.l('add_cst', 0, 'A8', 'A1'); a.bnop('mh_done', '!A1')
    a.ldw('A8', 16, 'A0'); a.nop(4)
    k(TRIM_HANDLER, 'A5'); a.l('sub', 'A0', 'A5', 'A1'); a.bnop('mh_done', 'A1')
    k(80, 'A9'); a.l('add', 'A8', 'A9', 'A9')
    a.ldw('A9', 16, 'A0'); a.nop(4)
    a.l('add_cst', 0, 'A0', 'A1'); a.bnop('mh_fill', '!A1')
    p.ref('rev_item', 'A5'); a.l('sub', 'A0', 'A5', 'A1'); a.bnop('mh_done', 'A1')
    a.label('mh_fill')
    k(20, 'B1'); a.l('add_cst', 0, 'A8', 'A2'); a.l('add_cst', 0, 'A9', 'A3')
    a.label('mh_copy')
    a.ldw('A2', 0, 'A0'); a.nop(4); a.stw('A0', 'A3', 0)
    a.addk(4, 'A2'); a.addk(4, 'A3'); a.addk(-1, 'B1'); a.bnop('mh_copy', 'B1')
    p.ref('S_REVERSE', 'A0'); a.stw('A0', 'A9', 0)
    p.ref('rev_item', 'A0'); a.stw('A0', 'A9', 16)
    k(0, 'A0'); a.stw('A0', 'A9', 4)
    a.stw('A9', 'A7', 8)
    k(3, 'A0'); a.stw('A0', 'A4', 0)
    a.label('mh_done')
    a.ldw('B15', 8, 'A4'); a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(16, 'B15')
    a.b(MENU_ENGINE); a.nop(5)
    # the menu handlers: card check as TRIM, load the flash code once, call into it with A4 = arg, return 0
    for name, target, arg in (('rev_item', CODEBUF, 0), ('app_item', LAUNCH, 0), ('util_item', LAUNCH, 1)):
        a.label(name)
        a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
        k(11, 'A4'); a.callp(CARD_CHECK)
        a.l('add_cst', 0, 'A4', 'A1'); a.bnop(f'{name}_ret', 'A1')
        a.callp('ensure_loaded')
        k(arg, 'A4'); k(0, 'B4')
        k(target, 'B5'); p.ref(f'{name}_back', 'B3'); a.raw(b_reg(5)); a.nop(5)
        a.label(f'{name}_back')
        k(0, 'A4')
        a.label(f'{name}_ret')
        a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15')
        a.raw(RET_B3); a.nop(5)
    a.label('ensure_loaded')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
    p.ref('LOADED', 'A3'); a.ldw('A3', 0, 'A1'); a.nop(4); a.bnop('el_ret', 'A1')
    k(BLOB_FLASH, 'A4'); k(CODEBUF, 'B4'); k(blob_len, 'A6'); a.callp(FLASH_READ)
    k(CODEBUF, 'A4'); k(blob_len, 'B4'); k(1, 'B6'); a.callp(CACHE_WBINV)
    a.callp(CACHE_INV_L1P)
    p.ref('LOADED', 'A3'); k(1, 'A0'); a.stw('A0', 'A3', 0)
    a.label('el_ret')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15')
    a.raw(RET_B3); a.nop(5)
    blob, addr = p.link([('LOADED', bytes(4)), ('S_REVERSE', b'REVERSE\0'), ('S_RUNAPP', b'RUN APP\0'), ('S_RUNUTIL', b'RUN UTIL\0')])
    assert CAVE + len(blob) <= CAVE_END, f'cave too big: {len(blob)}'
    return blob, a, addr

def patch(stock, launcher_out):
    d = bytearray(stock)
    off = file_map(d)
    bl = build_blob(launcher_out)
    assert BLOB_OFF + len(bl) <= PAD_END and all(b == 0xff for b in d[BLOB_OFF:BLOB_OFF + len(bl)])
    d[BLOB_OFF:BLOB_OFF + len(bl)] = bl
    cave, a, caddr = build_cave(len(bl))
    o = off(CAVE, len(cave)); d[o:o + len(cave)] = cave
    so = off(SITE, 4)
    w = struct.unpack_from('<I', d, so)[0]
    assert (w >> 28) == 1 and (w >> 2) & 0x1f == 0b00100
    assert ((SITE & ~31) + (asm6x_sext((w >> 7) & 0x1fffff, 21) << 2)) & 0xffffffff == MENU_ENGINE
    disp = (a.labels['menu_hook'] - (SITE & ~31)) // 4
    struct.pack_into('<I', d, so, (w & ~(0x1fffff << 7)) | ((disp & 0x1fffff) << 7))
    # after the cave code: RUN APP record, RUN UTIL record, the new 6-word SD CARD list
    rec_app = (CAVE + len(cave) + 3) & ~3
    rec_util = rec_app + 80
    new_list = rec_util + 80
    assert new_list + 24 <= CAVE_END, f'cave + records + list reach {new_list + 24:#x}'
    fo, lo, do_ = off(FORMAT_REC, 80), off(SD_LIST, 16), off(SD_DESC, 48)
    desc = struct.unpack_from('<12I', d, do_)
    stock_list = struct.unpack_from('<4I', d, lo)
    assert desc[0] == 3 and desc[8] == SD_LIST and stock_list[1] == FORMAT_REC and stock_list[3] == 0
    for rec, name, handler in ((rec_app, 'S_RUNAPP', 'app_item'), (rec_util, 'S_RUNUTIL', 'util_item')):
        r = bytearray(d[fo:fo + 80])
        struct.pack_into('<I', r, 0, caddr[name]); struct.pack_into('<I', r, 4, 0); struct.pack_into('<I', r, 16, a.labels[handler])
        ro = off(rec, 80); d[ro:ro + 80] = r
    lst = list(stock_list[:3]) + [rec_app, rec_util, 0]
    no = off(new_list, 24); struct.pack_into('<6I', d, no, *lst)
    struct.pack_into('<I', d, do_ + 32, new_list)                    # descriptor -> new list
    struct.pack_into('<I', d, do_, 5)                                # count 5
    struct.pack_into('<I', d, 0x34, r8fw.checksum(d))
    patch.extra = [(off(rec_app, 160), 160), (no, 24), (do_ + 32, 4), (do_, 4)]
    patch.addrs = dict(rec_app=rec_app, rec_util=rec_util, new_list=new_list, util_item=a.labels['util_item'], app_item=a.labels['app_item'])
    return bytes(d), (o, len(cave)), so, len(bl), a.labels

if __name__ == '__main__':
    src, lout, dst = sys.argv[1:4]
    stock = open(src, 'rb').read()
    assert hashlib.sha256(stock).hexdigest().startswith(STOCK_SHA), 'not stock v1.17'
    out, (co, cl), so, bl, labels = patch(stock, lout)
    diff = [i for i in range(len(out)) if out[i] != stock[i]]
    assert all(co <= i < co + cl or so <= i < so + 4 or 0x34 <= i < 0x38 or BLOB_OFF <= i < BLOB_OFF + bl
               or any(x <= i < x + n for x, n in patch.extra) for i in diff)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    print(f'cave {cl} bytes + 2 records + list; flash code {bl} bytes; {len(diff)} bytes changed; ' +
          ', '.join(f'{k} {v:#x}' for k, v in patch.addrs.items()))
    print('sha256', hashlib.sha256(out).hexdigest())
