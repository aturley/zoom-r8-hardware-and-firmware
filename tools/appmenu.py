#!/usr/bin/env python3
"""REVERSE in TRACK EDIT + RUN APP at the end of TOOL > SD CARD, for Zoom R8 firmware v1.17 (REVERSE v5 + app launcher).

Same patch sites as revmenu.py (menu_engine call in the TRACK EDIT builder, code cave, flash padding).
Flash padding now holds: the REVERSE code (asm, at CODEBUF 0xC0070000, < 8 KB) and, at LAUNCH (0xC0072000),
the app launcher (cbuild/launcher, C). Both are copied to RAM together, once, on first use.
menu_hook (TRACK EDIT, built at run time): as REVERSE v5, slot 2 = REVERSE, count = 3.
SD CARD is a static menu in the initialised data: descriptor 0xC06DFFA8 (count 3: EXCHANGE, FORMAT, REMAIN) with a
4-slot list at 0xC0715700 (slot 3 empty, the next menu's list follows). An 80-byte RUN APP record (FORMAT's record
with our name, handler and type 0 = no triangle) goes after the cave code; list slot 3 points at it, count = 4.
app_item: as rev_item, but calls the launcher's r8_entry. The launcher reads A:\\APP.BIN (see
cbuild/launcher/app.h); on a missing or bad file it shows a popup and returns to the menu, otherwise it takes the
machine over and never returns.
Usage: python3 appmenu.py STOCK_R8SYS.BIN cbuild/launcher/out/launch.out OUT/R8SYS.BIN
"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r8fw, celf, revmenu
from faderbar import file_map, asm6x_sext, STOCK_SHA
from menudump import Prog, b_reg, MENU_ENGINE, SITE, CAVE, CAVE_END, CODEBUF, BLOB_OFF, FLASH_READ, CACHE_WBINV, \
    CACHE_INV_L1P, BLOB_FLASH
from revmenu import TRIM_HANDLER, CARD_CHECK, RET_B3

LAUNCH = CODEBUF + 0x2000
SD_DESC, SD_LIST, FORMAT_REC = 0xC06DFFA8, 0xC0715700, 0xC06DFF08
PAD_END = 0x220040


def build_blob(launcher_out):
    rev, _ = revmenu.build_blob()
    assert len(rev) <= LAUNCH - CODEBUF, f'REVERSE code is {len(rev)} bytes'
    base, img, syms, top = celf.image(launcher_out)
    assert base == LAUNCH and syms.get('r8_entry') == LAUNCH, 'launcher must start with r8_entry at 0xC0072000'
    assert top <= 0xC0080000, f'launcher data reaches {top:#x} (APP_STAGE is 0xC0080000)'
    return rev + bytes(LAUNCH - CODEBUF - len(rev)) + img


def build_cave(blob_len):
    p = Prog(CAVE); a = p.a; k = p.k
    a.label('menu_hook')
    a.addk(-16, 'B15'); a.stw('B3', 'B15', 4); a.stw('A4', 'B15', 8)
    a.ldw('A4', 0, 'A0'); a.nop(4)
    k(2, 'A5'); a.l('sub', 'A0', 'A5', 'A1'); a.bnop('mh_done', 'A1')        # count must be 2
    a.ldw('A4', 32, 'A7'); a.nop(4)                                           # A7 = list
    a.ldw('A7', 4, 'A8'); a.nop(4)                                            # A8 = record 1
    a.l('add_cst', 0, 'A8', 'A1'); a.bnop('mh_done', '!A1')
    a.ldw('A8', 16, 'A0'); a.nop(4)
    k(TRIM_HANDLER, 'A5'); a.l('sub', 'A0', 'A5', 'A1'); a.bnop('mh_done', 'A1')   # record 1 must be TRIM
    k(80, 'A9'); a.l('add', 'A8', 'A9', 'A9')                                 # A9 = slot 2
    a.ldw('A9', 16, 'A0'); a.nop(4)                                           # empty or already ours
    a.l('add_cst', 0, 'A0', 'A1'); a.bnop('mh_fill', '!A1')
    p.ref('rev_item', 'A5'); a.l('sub', 'A0', 'A5', 'A1'); a.bnop('mh_done', 'A1')
    a.label('mh_fill')
    k(20, 'B1'); a.l('add_cst', 0, 'A8', 'A2'); a.l('add_cst', 0, 'A9', 'A3')
    a.label('mh_copy')
    a.ldw('A2', 0, 'A0'); a.nop(4); a.stw('A0', 'A3', 0)
    a.addk(4, 'A2'); a.addk(4, 'A3'); a.addk(-1, 'B1'); a.bnop('mh_copy', 'B1')
    p.ref('S_REVERSE', 'A0'); a.stw('A0', 'A9', 0)
    p.ref('rev_item', 'A0'); a.stw('A0', 'A9', 16)
    k(0, 'A0'); a.stw('A0', 'A9', 4)                                          # type 0: plain action, no triangle
    a.stw('A9', 'A7', 8)
    k(3, 'A0'); a.stw('A0', 'A4', 0)
    a.label('mh_done')
    a.ldw('B15', 8, 'A4'); a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(16, 'B15')
    a.b(MENU_ENGINE); a.nop(5)
    # ---- the two menu handlers: same early exit as TRIM, load the flash code once, call into it, return 0
    for name, target in (('rev_item', CODEBUF), ('app_item', LAUNCH)):
        a.label(name)
        a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
        k(11, 'A4'); a.callp(CARD_CHECK)
        a.l('add_cst', 0, 'A4', 'A1'); a.bnop(f'{name}_ret', 'A1')
        a.callp('ensure_loaded')
        k(0, 'A4'); k(0, 'B4')
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
    blob, addr = p.link([('LOADED', bytes(4)), ('S_REVERSE', b'REVERSE\0'), ('S_RUNAPP', b'RUN APP\0')])
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
    # RUN APP record after the cave code, hooked into the static SD CARD menu
    rec = (CAVE + len(cave) + 3) & ~3
    assert rec + 80 <= CAVE_END
    ro, fo, lo, co_ = off(rec, 80), off(FORMAT_REC, 80), off(SD_LIST, 16), off(SD_DESC, 4)
    assert struct.unpack_from('<I', d, co_)[0] == 3 and struct.unpack_from('<4I', d, lo)[1] == FORMAT_REC and struct.unpack_from('<I', d, lo + 12)[0] == 0
    r = bytearray(d[fo:fo + 80])
    struct.pack_into('<I', r, 0, caddr['S_RUNAPP']); struct.pack_into('<I', r, 4, 0); struct.pack_into('<I', r, 16, a.labels['app_item'])
    d[ro:ro + 80] = r
    struct.pack_into('<I', d, lo + 12, rec)
    struct.pack_into('<I', d, co_, 4)
    struct.pack_into('<I', d, 0x34, r8fw.checksum(d))
    patch.rec = rec; patch.extra = [(ro, 80), (lo + 12, 4), (co_, 4)]
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
    print(f'cave {cl} bytes; flash code {bl} bytes (REVERSE + launcher at {LAUNCH:#x}); {len(diff)} bytes changed')
    print('sha256', hashlib.sha256(out).hexdigest())
