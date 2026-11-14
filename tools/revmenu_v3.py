#!/usr/bin/env python3
"""REVERSE as a third TRACK EDIT item, for Zoom R8 firmware v1.17.

Patches (everything else stock):
  * 0xC072FEB8: CALLP menu_engine (0xC078ED80) in the TRACK EDIT builder -> CALLP menu_hook (displacement only).
  * Code cave 0xC06D9800: menu_hook, rev_item (the menu handler) and the loader.
  * File padding 0x212700 (flash 0x2926C0): the main code, assembled for 0xC0070000, loaded on first use with
    flash_read + Cache_wbInv + Cache_invL1pAll (proven by LOADTEST).
menu_hook (descriptor in A4; layout from the live RAM snapshot): if count == 2, record 1's handler is TRIM's and
  record slot 2 (record 1 + 80) is empty or ours: copy record 1 to slot 2, name = "REVERSE", handler = rev_item,
  list[2] = slot 2, count = 3. Then continue into the menu engine.
rev_item: like TRIM's handler, return early if 0xC0771DB8(11) != 0; load the main code once; call it; return 0.
Main code (track = word at 0xC0813024):
  path = 0xC077BDB4(track) (full path) -> 0: "No File!". 0xC07DAECC(path, info); info[0x133] & 1: "File Protected".
  0xC07F670C(); 0xC0798878("Please Wait...").
  temp = path with its last character replaced by '_' (same folder); delete any stale temp.
  Reverse path -> temp (chunk walk through the file, header copied unchanged, audio frames reversed in 32,760-byte
  blocks, as in the REVOUT test). Errors: delete temp, original untouched, popup ("Invalid File" / "File Access Error").
  Success: clear undo 0xC079240C(); delete(path); rename(temp, path); re-attach 0xC077BC14(path, track).
v3: detach the track's file before working on it (0xC077BCEC, as Trim does), re-attach on failure, and use the
first track of a stereo-linked pair (mask 0xC05B3240), as Trim and Stretch do.
v2 diagnostics: every run writes A:\\REVLOG.BIN (vars, paths, header, info, last 256 firmware calls + results).
Usage: python3 revmenu.py STOCK_R8SYS.BIN OUT/R8SYS.BIN
"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import r8fw
from faderbar import file_map, asm6x_sext, STOCK_SHA
from menudump import Prog, b_reg, MENU_ENGINE, SITE, TRACK_IDX, CAVE, CAVE_END, CODEBUF, BLOB_OFF, BLOB_FLASH, \
    FLASH_READ, CACHE_WBINV, CACHE_INV_L1P

OPEN, READ, WRITE, SEEK, CLOSE = 0xc0763c6c, 0xc0763b2c, 0xc0762db8, 0xc0764050, 0xc0765c6c
DELETE, RENAME = 0xc0762ee0, 0xc07639e8
TRACK_FILE, FILE_INFO, REATTACH, CLEAR_UNDO = 0xc077bdb4, 0xc07daecc, 0xc077bc14, 0xc079240c
CARD_CHECK = 0xc0771db8
PRE_MSG, SHOW_MSG, POPUP = 0xc07f670c, 0xc0798878, 0xc0797d90
S_PLEASE_WAIT, S_NO_FILE, S_PROTECTED, S_ACCESS_ERR, S_INVALID = 0xc06e77dd, 0xc06e77c5, 0xc06e77ce, 0xc06e7960, 0xc06e785d
TRIM_HANDLER = 0xc07317d8
DETACH = 0xc077bcec            # (track): wait for the streamer to go idle, clear the track's attached bit, release
STEREO_MASK = 0xc05b3240       # bit t set -> track t is stereo-linked: Trim/Stretch use track & ~1
BUF, HDR, INFO, TMP = 0xc0050000, 0xc0060000, 0xc0061000, 0xc0061400
CH = 32760
RET_B3 = 0x008ca362
LOGBUF, LOGMASK = 0xc0061800, 0x7f8            # ring of (call target, A4 result) pairs, 256 entries
VARS = ['MAGIC', 'LOGN', 'TRACK', 'PATH', 'FIN', 'FOUT', 'GOT', 'P', 'SZ', 'FMT', 'BA', 'DOFF', 'DLEN', 'REM', 'POS', 'N', 'REV', 'ERR']


def build_blob():
    p = Prog(CODEBUF); a = p.a; k = p.k
    V = {n: 4 * i for i, n in enumerate(VARS)}
    nfar = [0]
    def far(t, log=True):
        nfar[0] += 1; p.farcall(t, f'_far{nfar[0]}')
        if log:                                 # diagnostics: append (target, A4) to the ring; keeps A4
            getv('LOGN', 'A3'); k(LOGMASK, 'A5'); a.l('and', 'A3', 'A5', 'A5')
            k(LOGBUF, 'A0'); a.l('add', 'A0', 'A5', 'A5')
            k(t, 'A0'); a.stw('A0', 'A5', 0); a.stw('A4', 'A5', 4)
            a.l('add_cst', 8, 'A3', 'A3'); setv('LOGN', 'A3')
    def getv(n, r, n2=None, r2=None):
        a.ldw('A10', V[n], r)
        if n2: a.ldw('A10', V[n2], r2)
        a.nop(4)
    def setv(n, r): a.stw(r, 'A10', V[n])
    def setc(n, v): k(v, 'A0'); setv(n, 'A0')
    def addr_of(n, r): a.l('add_cst', 0, 'A10', r); a.addk(V[n], r)
    def fail_if(pred, code): a.mvk_p(code, 'A0', pred); a.bnop('fail', pred)
    def code_fail():
        a.l('add_cst', 0, 'A4', 'A0'); a.l('add_cst', 0, 'A4', 'A1'); a.bnop('fail', 'A1')
    def sub_err(code, lab): a.mvk_p(code, 'A4', 'A1'); a.bnop(lab, 'A1')
    def popup(s):
        k(s, 'A4'); far(POPUP)

    a.label('reverse_track')
    a.addk(-16, 'B15'); a.stw('B3', 'B15', 4); a.stw('A10', 'B15', 8)
    p.ref('VARS', 'A10')
    k(0, 'A0')
    for n in ('FIN', 'FOUT', 'FMT', 'BA', 'ERR', 'LOGN', 'DOFF', 'DLEN', 'P', 'SZ', 'GOT', 'N', 'REM', 'POS'): setv(n, 'A0')
    k(0x4c523852, 'A0'); setv('MAGIC', 'A0')                          # 'R8RL'
    k(TMP, 'A3'); k(0, 'A0'); a.stw('A0', 'A3', 0)
    k(TRACK_IDX, 'A3'); a.ldw('A3', 0, 'A4'); a.nop(4); setv('TRACK', 'A4')
    # stereo-linked tracks: like Trim, if bit 'track' is set in STEREO_MASK use track & ~1
    k(1, 'A0'); a.l('add_cst', 0, 'A4', 'B1')
    a.label('bit'); a.bnop('bit_done', '!B1'); a.l('add', 'A0', 'A0', 'A0'); a.addk(-1, 'B1'); a.bnop('bit')
    a.label('bit_done')
    k(STEREO_MASK, 'A3'); a.ldw('A3', 0, 'A5'); a.nop(4); a.l('and', 'A0', 'A5', 'A1')
    a.bnop('mono', '!A1')
    getv('TRACK', 'A0'); k(-2, 'A5'); a.l('and', 'A0', 'A5', 'A0'); setv('TRACK', 'A0')
    a.label('mono')
    getv('TRACK', 'A4'); far(TRACK_FILE); setv('PATH', 'A4')
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('have_file', 'A1')
    popup(S_NO_FILE); a.bnop('out')
    a.label('have_file')
    getv('PATH', 'A4'); k(INFO, 'B4'); far(FILE_INFO)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('info_ok', '!A1')
    popup(S_ACCESS_ERR); a.bnop('out')
    a.label('info_ok')
    k(INFO + 0x133, 'A3'); a.ldst2('ldbu', 'A3', 0, 'A0'); a.nop(4)
    k(1, 'A5'); a.l('and', 'A0', 'A5', 'A1'); a.bnop('writable', '!A1')
    popup(S_PROTECTED); a.bnop('out')
    a.label('writable')
    far(PRE_MSG)
    k(S_PLEASE_WAIT, 'A4'); far(SHOW_MSG)
    # temp path = path with the last character replaced by '_'
    getv('PATH', 'A7'); k(TMP, 'A8')
    a.label('cp')
    a.ldst2('ldbu', 'A7', 1, 'A0', 'post++'); a.nop(4)
    a.ldst2('stb', 'A8', 1, 'A0', 'post++')
    a.l('add_cst', 0, 'A0', 'A1'); a.bnop('cp', 'A1')
    a.l('add_cst', -2, 'A8', 'A8'); k(0x5f, 'A0'); a.ldst2('stb', 'A8', 0, 'A0')
    k(TMP, 'A4'); far(DELETE)                                           # stale temp from an earlier failure
    getv('TRACK', 'A4'); far(DETACH)                                    # release the file (as Trim does)
    # open input, walk chunks
    addr_of('FIN', 'A4'); getv('PATH', 'B4'); k(0, 'A6'); k(0, 'B6'); far(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('opened', '!A1')
    setc('FIN', 0); k(6, 'A0'); a.bnop('fail')
    a.label('opened')
    setc('P', 0); a.callp('rdhdr'); code_fail()
    k(HDR + 8, 'A4'); a.callp('le4'); k(0x45564157, 'A5'); a.l('sub', 'A4', 'A5', 'A1'); fail_if('A1', 2)
    setc('P', 12)
    a.label('chunk')
    a.callp('rdhdr'); code_fail()
    getv('GOT', 'A0'); k(8, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); fail_if('A1', 3)
    k(HDR + 4, 'A4'); a.callp('le4'); setv('SZ', 'A4')
    k(HDR, 'A4'); a.callp('le4')
    k(0x61746164, 'A5'); a.l('sub', 'A4', 'A5', 'A2'); a.bnop('not_data', 'A2')
    getv('P', 'A0'); a.l('add_cst', 8, 'A0', 'A0'); setv('DOFF', 'A0')
    getv('SZ', 'A0'); setv('DLEN', 'A0'); a.bnop('found')
    a.label('not_data')
    k(0x20746d66, 'A5'); a.l('sub', 'A4', 'A5', 'A2'); a.bnop('not_fmt', 'A2')
    getv('GOT', 'A0'); k(24, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); fail_if('A1', 3)
    k(HDR + 20, 'A4'); k(2, 'B4'); a.callp('le'); setv('BA', 'A4'); setc('FMT', 1)
    a.label('not_fmt')
    getv('P', 'A7', 'SZ', 'A5')
    a.l('add', 'A7', 'A5', 'A0'); a.l('add_cst', 8, 'A0', 'A0')
    k(1, 'A9'); a.l('and', 'A5', 'A9', 'A5'); a.l('add', 'A0', 'A5', 'A0')
    a.l('cmpltu', 'A0', 'A7', 'A1'); fail_if('A1', 3)
    setv('P', 'A0'); a.bnop('chunk')
    a.label('found')
    getv('FMT', 'A1'); fail_if('!A1', 3)
    getv('BA', 'A0'); a.l('add_cst', -1, 'A0', 'A0'); k(8, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); fail_if('!A1', 3)
    # whole frames only (a partial frame would shift every sample)
    getv('DLEN', 'A0'); k(CH, 'A5')
    a.label('m1'); a.l('cmpltu', 'A0', 'A5', 'A1'); a.bnop('m2', 'A1'); a.l('sub', 'A0', 'A5', 'A0'); a.bnop('m1')
    a.label('m2'); getv('BA', 'A5')
    a.label('m3'); a.l('cmpltu', 'A0', 'A5', 'A1'); a.bnop('m4', 'A1'); a.l('sub', 'A0', 'A5', 'A0'); a.bnop('m3')
    a.label('m4'); a.l('add_cst', 0, 'A0', 'A1'); fail_if('A1', 3)
    # output: header unchanged, then audio reversed
    addr_of('FOUT', 'A4'); k(TMP, 'B4'); k(0x101, 'A6'); k(0x80, 'B6'); far(OPEN)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('out_ok', '!A1')
    setc('FOUT', 0); k(7, 'A0'); a.bnop('fail')
    a.label('out_ok')
    getv('DOFF', 'A0'); setv('REM', 'A0'); setc('POS', 0); setc('REV', 0)
    a.label('hcopy')
    a.callp('next_n'); a.l('add_cst', 0, 'A0', 'A1'); a.bnop('hdone', '!A1')
    a.callp('xfer'); code_fail()
    getv('POS', 'A0', 'N', 'A5'); a.l('add', 'A0', 'A5', 'A0'); setv('POS', 'A0')
    a.callp('rem_n'); a.bnop('hcopy')
    a.label('hdone')
    getv('DLEN', 'A0', 'DOFF', 'A5'); setv('REM', 'A0'); a.l('add', 'A0', 'A5', 'A0'); setv('POS', 'A0')
    setc('REV', 1)
    a.label('blk')
    a.callp('next_n'); a.l('add_cst', 0, 'A0', 'A1'); a.bnop('io_done', '!A1')
    getv('POS', 'A5'); a.l('sub', 'A5', 'A0', 'A5'); setv('POS', 'A5')
    a.callp('xfer'); code_fail()
    a.callp('rem_n'); a.bnop('blk')

    a.label('fail')
    setv('ERR', 'A0')
    a.label('io_done')
    for fd in ('FOUT', 'FIN'):
        getv(fd, 'A4'); a.l('add_cst', 0, 'A4', 'A1'); a.bnop(f'nc_{fd}', '!A1')
        far(CLOSE); a.label(f'nc_{fd}')
    getv('ERR', 'A1'); a.bnop('swap', '!A1')
    k(TMP, 'A4'); far(DELETE)                                           # failure: drop the temp, keep the original
    getv('PATH', 'A4'); getv('TRACK', 'B4'); far(REATTACH)              # and give the original back to the track
    getv('ERR', 'A0'); k(4, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1')       # codes 2..3: not a usable WAV
    a.bnop('io_err', '!A1')
    popup(S_INVALID); a.bnop('out')
    a.label('io_err')
    popup(S_ACCESS_ERR); a.bnop('out')
    a.label('swap')
    far(CLEAR_UNDO)
    getv('PATH', 'A4'); far(DELETE)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('del_ok', '!A1')
    k(TMP, 'A4'); far(DELETE)
    getv('PATH', 'A4'); getv('TRACK', 'B4'); far(REATTACH)
    popup(S_ACCESS_ERR); a.bnop('out')
    a.label('del_ok')
    k(TMP, 'A4'); getv('PATH', 'B4'); far(RENAME)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('ren_ok', '!A1')
    popup(S_ACCESS_ERR)
    a.label('ren_ok')
    getv('PATH', 'A4'); getv('TRACK', 'B4'); far(REATTACH)
    a.label('out')
    # diagnostics: A:\\REVLOG.BIN = vars, path, temp path, last chunk header, file info, call log
    addr_of('FIN', 'A4'); p.ref('P_LOG', 'B4'); k(0x101, 'A6'); k(0x80, 'B6'); far(OPEN, log=False)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('nolog', 'A1')
    for src, n in (('VARS', 4 * len(VARS)), ('PATH', 128), (TMP, 128), (HDR, 64), (INFO, 0x140), (LOGBUF, LOGMASK + 8)):
        getv('FIN', 'A4')
        if src == 'VARS': a.l('add_cst', 0, 'A10', 'B4')
        elif src == 'PATH':
            nfar[0] += 1; lab = f'_lp{nfar[0]}'
            getv('PATH', 'B4'); a.l('add_cst', 0, 'B4', 'B0'); a.bnop(lab, 'B0')
            k(TMP, 'B4')                                                   # no path: log the (empty) temp buffer
            a.label(lab)
        else: k(src, 'B4')
        k(n, 'A6'); addr_of('GOT', 'B6'); far(WRITE, log=False)
    getv('FIN', 'A4'); far(CLOSE, log=False)
    a.label('nolog')
    a.ldw('B15', 8, 'A10'); a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(16, 'B15')
    k(0, 'A4')
    a.raw(RET_B3); a.nop(5)

    ret = lambda: (a.raw(RET_B3), a.nop(5))
    a.label('next_n')
    getv('REM', 'A0'); k(CH, 'A5'); a.l('cmpltu', 'A0', 'A5', 'A1'); a.bnop('nn', 'A1')
    a.l('add_cst', 0, 'A5', 'A0')
    a.label('nn'); setv('N', 'A0'); ret()
    a.label('rem_n')
    getv('REM', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A0'); setv('REM', 'A0'); ret()
    for name in ('rdhdr', 'xfer'):
        a.label(name)
        a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
        pos = 'P' if name == 'rdhdr' else 'POS'
        getv('FIN', 'A4', pos, 'B4'); k(2, 'A6'); far(SEEK)
        a.l('add_cst', 0, 'A4', 'A1'); sub_err(6, f'{name}_ret')
        if name == 'rdhdr':
            getv('FIN', 'A4'); k(HDR, 'B4'); k(24, 'A6'); addr_of('GOT', 'B6'); far(READ)
            a.l('add_cst', 0, 'A4', 'A1'); sub_err(6, f'{name}_ret')
        else:
            getv('FIN', 'A4', 'N', 'A6'); k(BUF, 'B4'); addr_of('GOT', 'B6'); far(READ)
            a.l('add_cst', 0, 'A4', 'A1'); sub_err(6, f'{name}_ret')
            getv('GOT', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A1'); sub_err(6, f'{name}_ret')
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
            getv('FOUT', 'A4', 'N', 'A6'); k(BUF, 'B4'); addr_of('GOT', 'B6'); far(WRITE)
            a.l('add_cst', 0, 'A4', 'A1'); sub_err(7, f'{name}_ret')
            getv('GOT', 'A0', 'N', 'A5'); a.l('sub', 'A0', 'A5', 'A1'); sub_err(7, f'{name}_ret')
        k(0, 'A4')
        a.label(f'{name}_ret')
        a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15'); ret()
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
    return p.link([('VARS', bytes(4 * len(VARS))), ('P_LOG', b'A:\\REVLOG.BIN\0')])


def build_cave(blob_len):
    p = Prog(CAVE); a = p.a; k = p.k
    # ---- menu_hook(A4 = descriptor, ...): add REVERSE as item 3, then continue into the menu engine
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
    a.ldw('A9', 16, 'A0'); a.nop(4)                                           # slot 2 empty or already ours
    a.l('add_cst', 0, 'A0', 'A1'); a.bnop('mh_fill', '!A1')
    p.ref('rev_item', 'A5'); a.l('sub', 'A0', 'A5', 'A1'); a.bnop('mh_done', 'A1')
    a.label('mh_fill')
    k(20, 'B1'); a.l('add_cst', 0, 'A8', 'A2'); a.l('add_cst', 0, 'A9', 'A3')
    a.label('mh_copy')
    a.ldw('A2', 0, 'A0'); a.nop(4); a.stw('A0', 'A3', 0)
    a.addk(4, 'A2'); a.addk(4, 'A3'); a.addk(-1, 'B1'); a.bnop('mh_copy', 'B1')
    p.ref('S_REVERSE', 'A0'); a.stw('A0', 'A9', 0)
    p.ref('rev_item', 'A0'); a.stw('A0', 'A9', 16)
    a.stw('A9', 'A7', 8)
    k(3, 'A0'); a.stw('A0', 'A4', 0)
    a.label('mh_done')
    a.ldw('B15', 8, 'A4'); a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(16, 'B15')
    a.b(MENU_ENGINE); a.nop(5)
    # ---- rev_item(): the REVERSE menu handler
    a.label('rev_item')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
    k(11, 'A4'); a.callp(CARD_CHECK)
    a.l('add_cst', 0, 'A4', 'A1'); a.bnop('ri_ret', 'A1')                    # same early exit as TRIM
    p.ref('LOADED', 'A3'); a.ldw('A3', 0, 'A1'); a.nop(4); a.bnop('ri_go', 'A1')
    k(BLOB_FLASH, 'A4'); k(CODEBUF, 'B4'); k(blob_len, 'A6'); a.callp(FLASH_READ)
    k(CODEBUF, 'A4'); k(blob_len, 'B4'); k(1, 'B6'); a.callp(CACHE_WBINV)
    a.callp(CACHE_INV_L1P)
    p.ref('LOADED', 'A3'); k(1, 'A0'); a.stw('A0', 'A3', 0)
    a.label('ri_go')
    k(CODEBUF, 'B5'); p.ref('ri_back', 'B3'); a.raw(b_reg(5)); a.nop(5)
    a.label('ri_back')
    k(0, 'A4')
    a.label('ri_ret')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15')
    a.raw(RET_B3); a.nop(5)
    blob, addr = p.link([('LOADED', bytes(4)), ('S_REVERSE', b'REVERSE\0')])
    assert CAVE + len(blob) <= CAVE_END, f'cave too big: {len(blob)}'
    return blob, a, addr


def patch(stock):
    d = bytearray(stock)
    off = file_map(d)
    bl, baddr = build_blob()
    assert BLOB_OFF + len(bl) <= 0x220040 and all(b == 0xff for b in d[BLOB_OFF:BLOB_OFF + len(bl)])
    d[BLOB_OFF:BLOB_OFF + len(bl)] = bl
    cave, a, caddr = build_cave(len(bl))
    o = off(CAVE, len(cave)); d[o:o + len(cave)] = cave
    so = off(SITE, 4)
    w = struct.unpack_from('<I', d, so)[0]
    assert (w >> 28) == 1 and (w >> 2) & 0x1f == 0b00100
    assert ((SITE & ~31) + (asm6x_sext((w >> 7) & 0x1fffff, 21) << 2)) & 0xffffffff == MENU_ENGINE
    disp = (a.labels['menu_hook'] - (SITE & ~31)) // 4
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
    print(f'cave {cl} bytes; main code {bl} bytes in flash padding; {len(diff)} bytes changed')
    print('sha256', hashlib.sha256(out).hexdigest())
