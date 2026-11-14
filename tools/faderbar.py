#!/usr/bin/env python3
"""Fader 1 -> LCD bar test for Zoom R8 firmware v1.17.

One patch to the stock image, plus code in the XDC Text name table cave:
  * RX call site 0xC07BBCF8: CALLP uart_read -> CALLP rx_hook (displacement field only)
rx_hook calls uart_read exactly as before, then looks at the byte it returned:
  * byte >= 0x80  -> remember it as the current status byte
  * byte <  0x80 and status == 0xC3 (fader 1) -> value = byte (0..127); draw a 128-column bar
    into BUF (columns 0..value = 0x7E, rest = 0x42, i.e. a hollow box filling up) and call
    lcd_put(PAGE*128, 128, BUF), which copies it into the LCD driver's target frame and wakes
    the LCD hardware task.
The firmware still gets every byte, so the panel works as normal. The firmware's own drawing
will repaint the band whenever it redraws it; moving fader 1 draws the bar again.

Usage: python3 faderbar.py STOCK_R8SYS.BIN OUT/R8SYS.BIN
"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import asm6x, r8fw

UART_READ = 0xc07d54a8
LCD_PUT = 0xc07ccf34
RX_SITE = 0xc07bbcf8
CAVE, CAVE_END = 0xc06d9800, 0xc06d9de0
DATA = 0xc06d9d00
STATE, BUF = DATA, DATA + 16
FADER = 0xC3            # fader 1 status byte (from the panel-link capture)
PAGE = 7                # bottom 8-pixel band (bands 0..7)
FULL, EMPTY = 0x7E, 0x42
RET_B3 = 0x008ca362     # firmware's own "B .S2 B3" word
STOCK_SHA = '6e4bf64e'


def build():
    a = asm6x.Asm(CAVE)
    a.label('rx_hook')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A6', 'B15', 8); a.stw('B6', 'B15', 12)
    a.callp(UART_READ)                                   # same args as the original call
    a.stw('A4', 'B15', 16)                               # keep uart_read's return value
    a.ldw('B15', 12, 'A3'); a.nop(4)
    a.ldst2('ldw', 'A3', 0, 'B1'); a.nop(4)              # B1 = bytes read
    a.b('out', '!B1'); a.nop(5)
    a.ldw('B15', 8, 'A6'); a.nop(4)
    a.ldst2('ldbu', 'A6', 0, 'A5'); a.nop(4)             # A5 = the byte
    a.mvk32(STATE, 'A3')
    a.mvkl(0x7F, 'A0'); a.mvkh(0, 'A0')
    a.l('cmpltu', 'A0', 'A5', 'A1')                      # A1 = byte is a status byte
    a.b('data', '!A1'); a.nop(5)
    a.stw('A5', 'A3', 0)                                 # status byte: remember it
    a.b('out'); a.nop(5)
    a.label('data')
    a.ldw('A3', 0, 'A7'); a.nop(4)                       # A7 = current status
    a.mvkl(FADER, 'A0'); a.mvkh(0, 'A0')
    a.l('sub', 'A7', 'A0', 'A2')
    a.b('out', 'A2'); a.nop(5)                           # not fader 1 -> done
    # draw: for x in 0..127: BUF[x] = FULL if x <= value else EMPTY
    a.mvk32(BUF, 'A7')
    a.mvkl(0, 'A9'); a.mvkh(0, 'A9')                     # A9 = x
    a.mvkl(128, 'B1'); a.mvkh(0, 'B1')                   # B1 = count
    a.label('fill')
    a.l('cmpltu', 'A5', 'A9', 'A2')                      # A2 = value < x  (empty part)
    a.mvkl(FULL, 'A0'); a.mvkh(0, 'A0')
    a.mvk_p(EMPTY, 'A0', 'A2')
    a.ldst2('stb', 'A7', 1, 'A0', 'post++')
    a.l('add_cst', 1, 'A9', 'A9')
    a.addk(-1, 'B1')
    a.b('fill', 'B1'); a.nop(5)
    a.mvkl(PAGE * 128, 'A4'); a.mvkh(0, 'A4')
    a.mvkl(128, 'B4'); a.mvkh(0, 'B4')
    a.mvk32(BUF, 'A6')
    a.callp(LCD_PUT)
    a.label('out')
    a.ldw('B15', 16, 'A4'); a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(32, 'B15')
    a.raw(RET_B3); a.nop(5)
    code = a.assemble()
    assert CAVE + len(code) <= DATA, f'code too long: {len(code)}'
    blob = code + b'\0' * (DATA - CAVE - len(code)) + bytes(16 + 128)
    assert CAVE + len(blob) <= CAVE_END
    return blob, a


def file_map(d):
    cmds, _ = r8fw.parse_ais(d)
    secs = [(c[2], c[3], c[4]) for c in cmds if c[0] == 'SectionLoad']
    def off(addr, n=1):
        for a0, sz, fo in secs:
            if a0 <= addr and addr + n <= a0 + sz:
                return fo + addr - a0
        raise ValueError(hex(addr))
    return off


def patch(stock):
    d = bytearray(stock)
    off = file_map(d)
    blob, a = build()
    o = off(CAVE, len(blob)); d[o:o + len(blob)] = blob
    so = off(RX_SITE, 4)
    w = struct.unpack_from('<I', d, so)[0]
    assert (w >> 28) == 1 and (w >> 2) & 0x1f == 0b00100, f'unexpected word at RX site: {w:#x}'
    old_t = ((RX_SITE & ~31) + (asm6x_sext((w >> 7) & 0x1fffff, 21) << 2)) & 0xffffffff
    assert old_t == UART_READ, f'RX site calls {old_t:#x}'
    disp = (a.labels['rx_hook'] - (RX_SITE & ~31)) // 4
    w2 = (w & ~(0x1fffff << 7)) | ((disp & 0x1fffff) << 7)
    struct.pack_into('<I', d, so, w2)
    struct.pack_into('<I', d, 0x34, r8fw.checksum(d))
    return bytes(d), a, blob, (o, so)


def asm6x_sext(v, n):
    return v - (1 << n) if v & (1 << (n - 1)) else v


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    stock = open(src, 'rb').read()
    sha = hashlib.sha256(stock).hexdigest()
    assert sha.startswith(STOCK_SHA), f'not stock v1.17 ({sha[:8]})'
    out, a, blob, (co, so) = patch(stock)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    diff = [i for i in range(len(out)) if out[i] != stock[i]]
    print(f'labels {{{", ".join(f"{k}: {v:#x}" for k, v in a.labels.items())}}}')
    print(f'{len(diff)} bytes changed; cave file range {co:#x}+{len(blob):#x}, site {so:#x}')
    print('sha256', hashlib.sha256(out).hexdigest())
