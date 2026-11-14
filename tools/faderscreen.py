#!/usr/bin/env python3
"""Full-screen fader display test for Zoom R8 firmware v1.17.

Patch (all other bytes stock): RX call site 0xC07BBCF8 CALLP uart_read -> CALLP rx_hook (displacement only).
rx_hook calls uart_read as before (the firmware still gets every byte), then:
  * status byte (>= 0x80): remembered.
  * data byte after status 0xC0..0xCB: slot = status - 0xC0 (12 slots), top = 63 - value/2 is stored.
    Takeover happens for 0xC3..0xCB (real faders), or for 0xC0..0xC2 only while the screen is already ours
    (0xC1 drifts on its own and must not trigger it). On takeover/redraw:
        freeze timestamp (0xC081311C) = now (ms tick at 0xC0813878); freeze byte (0xC081311A) = 1;
        dirty mask (0xC081310C) = 0xFF  -> full repaint when the firmware's screen comes back;
        render 12 vertical bars into FRAME (1 KB, 0xC0050000) and lcd_put(0, 1024, FRAME).
  * data byte 0x03 after status 0xA0 (STOP pressed): freeze byte = 0 (screen given back).
The LCD task clears the freeze by itself 5 s after the last timestamp, so the normal screen also
returns 5 s after the last fader movement.

Layout: slot s (status 0xC0+s) occupies columns 10s .. 10s+9: 1 blank, 8 bar, 1 blank; columns 120..127 blank.
A bar covers rows top..63 (row 63 = bottom), so value 0 shows a 1-pixel baseline and 127 a full-height bar.
Assumes bit k of byte (band*128 + column) is row band*8+k; if that is wrong the bars will look broken.

Usage: python3 faderscreen.py STOCK_R8SYS.BIN OUT/R8SYS.BIN
"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import asm6x, r8fw
from faderbar import file_map, asm6x_sext, STOCK_SHA

UART_READ = 0xc07d54a8
LCD_PUT = 0xc07ccf34
RX_SITE = 0xc07bbcf8
CAVE, CAVE_END = 0xc06d9800, 0xc06d9de0
TABS = 0xc06d9c00
STATE, TOPS, TOPTAB, MASKTAB = TABS, TABS + 4, TABS + 16, TABS + 16 + 128
TAB_END = MASKTAB + 120
FRAME = 0xc0050000
NOW_MS = 0xc0813878          # B14+0xd58
FREEZE = 0xc081311a          # B14+0x5fa (byte)
FREEZE_T = 0xc081311c        # B14+0x5fc
DIRTY = 0xc081310c           # B14+0x5ec
RET_B3 = 0x008ca362
NSLOT = 12


def tables():
    tops = bytes([63] * NSLOT)
    toptab = bytes(63 - (v >> 1) for v in range(128))
    mask = bytearray()
    for e in range(120):
        d = e - 56
        mask.append(0xFF if d <= 0 else 0 if d >= 8 else (0xFF << d) & 0xFF)
    return tops, toptab, bytes(mask)


def build():
    a = asm6x.Asm(CAVE)
    k = lambda v, r: (a.mvkl(v, r), a.mvkh(v, r))
    a.label('rx_hook')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A6', 'B15', 8); a.stw('B6', 'B15', 12)
    a.callp(UART_READ)
    a.stw('A4', 'B15', 16)
    a.ldw('B15', 12, 'A3'); a.nop(4)
    a.ldst2('ldw', 'A3', 0, 'B1'); a.nop(4)              # bytes read
    a.b('out', '!B1'); a.nop(5)
    a.ldw('B15', 8, 'A6'); a.nop(4)
    a.ldst2('ldbu', 'A6', 0, 'A5'); a.nop(4)             # A5 = byte
    k(STATE, 'A3')
    k(0x7F, 'A0')
    a.l('cmpltu', 'A0', 'A5', 'A1')
    a.b('data', '!A1'); a.nop(5)
    a.stw('A5', 'A3', 0)                                 # status byte
    a.b('out'); a.nop(5)

    a.label('data')
    a.ldw('A3', 0, 'A7'); a.nop(4)                       # A7 = status
    # STOP press (A0 03): give the screen back
    k(0xA0, 'A0'); a.l('sub', 'A7', 'A0', 'A1')
    a.b('notstop', 'A1'); a.nop(5)
    k(0x03, 'A0'); a.l('sub', 'A5', 'A0', 'A1')
    a.b('out', 'A1'); a.nop(5)
    k(FREEZE, 'A3'); k(0, 'A0'); a.ldst2('stb', 'A3', 0, 'A0')
    a.b('out'); a.nop(5)

    a.label('notstop')
    k(0xC0, 'A0'); a.l('sub', 'A7', 'A0', 'A8')          # A8 = slot
    k(NSLOT, 'A0'); a.l('cmpltu', 'A8', 'A0', 'A1')      # slot < 12 ?
    a.b('out', '!A1'); a.nop(5)
    # TOPS[slot] = TOPTAB[value]
    k(TOPTAB, 'A3'); a.l('add', 'A3', 'A5', 'A3')
    a.ldst2('ldbu', 'A3', 0, 'A0'); a.nop(4)
    k(TOPS, 'A3'); a.l('add', 'A3', 'A8', 'A3')
    a.ldst2('stb', 'A3', 0, 'A0')
    # trigger: slot >= 3, or the screen is already frozen
    k(3, 'A0'); a.l('cmpltu', 'A8', 'A0', 'A1')          # A1 = slot < 3
    a.b('take', '!A1'); a.nop(5)
    k(FREEZE, 'A3'); a.ldst2('ldbu', 'A3', 0, 'A2'); a.nop(4)
    a.b('out', '!A2'); a.nop(5)

    a.label('take')
    k(NOW_MS, 'A3'); a.ldst2('ldw', 'A3', 0, 'A0'); a.nop(4)
    k(FREEZE_T, 'A3'); a.ldst2('stw', 'A3', 0, 'A0')
    k(FREEZE, 'A3'); k(1, 'A0'); a.ldst2('stb', 'A3', 0, 'A0')
    k(DIRTY, 'A3'); k(0xFF, 'A0'); a.ldst2('stw', 'A3', 0, 'A0')
    # render: for band b (B2 = 8..1, B5 = 56 - 8b), for slot s: byte = MASKTAB[TOPS[s] + B5]
    k(FRAME, 'A7')
    k(MASKTAB, 'A9')
    k(0, 'A6')
    k(8, 'B2')
    k(56, 'B5')
    a.label('band')
    k(TOPS, 'A8')
    k(NSLOT, 'B1')
    a.label('slot')
    a.ldst2('ldbu', 'A8', 1, 'A3', 'post++'); a.nop(4)   # A3 = top
    a.l('add', 'A3', 'B5', 'A3')                         # + 56 - 8b
    a.l('add', 'A3', 'A9', 'A3')
    a.ldst2('ldbu', 'A3', 0, 'A0'); a.nop(4)
    a.ldst2('stb', 'A7', 1, 'A6', 'post++')
    for _ in range(8):
        a.ldst2('stb', 'A7', 1, 'A0', 'post++')
    a.ldst2('stb', 'A7', 1, 'A6', 'post++')
    a.addk(-1, 'B1')
    a.b('slot', 'B1'); a.nop(5)
    for _ in range(128 - 10 * NSLOT):
        a.ldst2('stb', 'A7', 1, 'A6', 'post++')
    a.addk(-8, 'B5')
    a.addk(-1, 'B2')
    a.b('band', 'B2'); a.nop(5)
    k(0, 'A4'); k(1024, 'B4'); k(FRAME, 'A6')
    a.callp(LCD_PUT)

    a.label('out')
    a.ldw('B15', 16, 'A4'); a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(32, 'B15')
    a.raw(RET_B3); a.nop(5)
    code = a.assemble()
    assert CAVE + len(code) <= TABS, f'code too long: {len(code)}'
    tops, toptab, mask = tables()
    data = struct.pack('<I', 0) + tops + bytes(16 - 4 - NSLOT) + toptab + mask
    assert TABS + len(data) == TAB_END <= CAVE_END
    blob = code + b'\0' * (TABS - CAVE - len(code)) + data
    return blob, a


def patch(stock):
    d = bytearray(stock)
    off = file_map(d)
    blob, a = build()
    o = off(CAVE, len(blob)); d[o:o + len(blob)] = blob
    so = off(RX_SITE, 4)
    w = struct.unpack_from('<I', d, so)[0]
    assert (w >> 28) == 1 and (w >> 2) & 0x1f == 0b00100
    assert ((RX_SITE & ~31) + (asm6x_sext((w >> 7) & 0x1fffff, 21) << 2)) & 0xffffffff == UART_READ
    disp = (a.labels['rx_hook'] - (RX_SITE & ~31)) // 4
    struct.pack_into('<I', d, so, (w & ~(0x1fffff << 7)) | ((disp & 0x1fffff) << 7))
    struct.pack_into('<I', d, 0x34, r8fw.checksum(d))
    return bytes(d), a, blob, (o, so)


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    stock = open(src, 'rb').read()
    sha = hashlib.sha256(stock).hexdigest()
    assert sha.startswith(STOCK_SHA), f'not stock v1.17 ({sha[:8]})'
    out, a, blob, (co, so) = patch(stock)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    diff = [i for i in range(len(out)) if out[i] != stock[i]]
    assert all(co <= i < co + len(blob) or so <= i < so + 4 or 0x34 <= i < 0x38 for i in diff)
    print(f'code {a.labels["out"] - CAVE + 40} bytes; {len(diff)} bytes changed (cave, RX site, checksum only)')
    print('sha256', hashlib.sha256(out).hexdigest())
