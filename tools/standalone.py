#!/usr/bin/env python3
"""Standalone LCD test-pattern firmware for the Zoom R8 (replaces R8SYS's boot script; no Zoom code runs).

R8SYS.BIN layout kept: the 64-byte header (tag, version, checksum) and the file size are unchanged.
The AIS boot script at 0x40 is replaced by:
    magic, SeqReadEnable, FunctionExec PLL, FunctionExec DDR   (copied byte-for-byte from stock 1.17)
    SectionLoad 0x11800000 <our program + data>                 (L2 RAM, where stock also loads code)
    JumpClose 0x11800000
Everything after that is the untouched remainder of the stock file (never read: the loader stops at JumpClose).

The program (C674x, polled, interrupts off):
  1. CSR.GIE = 0, IER = 0, ICR = 0xFFF0; stack in L2.
  2. PSC1: enable LPSC 3 (GPIO) and 10 (SPI1)  (MDCTL.NEXT = 3, GO, wait PTSTAT).
  3. SYSCFG: unlock KICK0/1, write the pin-mux values stock R8SYS writes (PINMUX0-3, 5-19; PINMUX4 is board-
     revision dependent and is left as the boot loader set it).
  4. GPIO: CS = GP2[14], A0 = GP8[9], RESET = GP8[8]; drive high, then make them outputs.
  5. SPI1 (0x01F0E000): master, PC0 = 0x0E01, DELAY = 0x03030000, FMT0 = prescale 17 (180 MHz/18 = 10 MHz),
     polarity 1, phase 0, 8-bit, MSB first; enable.
  6. LCD init script = stock's: reset pulses, AE A2 A0 C0 26 81 0D A6 A4 40 B0 10 00 2C,
     (2D 2F 2C)x3 2D 2F with 10 ms gaps, contrast 81 0D, display on AF.
  7. Send the 1 KB test frame (8 bands: B0+band, 00, 10, then 128 data bytes).
  8. Forever: every 500 ms toggle a heartbeat block in the top-right corner.

Usage: python3 standalone.py STOCK_R8SYS.BIN OUT/R8SYS.BIN [preview.png]
"""
import os, struct, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import asm6x, r8fw
from faderbar import STOCK_SHA

BASE = 0x11800000
STACK_TOP = 0x11810000
RET_B3 = 0x008ca362
PSC1 = 0x01E27000
SYSCFG0 = 0x01C14000
GPIO = 0x01E26000
DIR23, SET23, CLR23 = GPIO + 0x38, GPIO + 0x40, GPIO + 0x44
DIR8, SET8, CLR8 = GPIO + 0xB0, GPIO + 0xB8, GPIO + 0xBC
CS_BIT, A0_BIT, RST_BIT = 1 << 14, 1 << 9, 1 << 8
SPI1 = 0x01F0E000
PINMUX = {0: 0x88111111, 1: 0x88888888, 2: 0x01141248, 3: 0x44441111, 5: 0x88188188, 6: 0x88888888,
          7: 0x88888888, 8: 0x88888888, 9: 0x88888888, 10: 0x88888888, 11: 0x88888888, 12: 0x88888888,
          13: 0x88888888, 14: 0x00000088, 15: 0x88888800, 16: 0x88888888, 17: 0x00088888, 18: 0x88822280,
          19: 0x18822288}
LOOPS_PER_MS = 60000         # ~7 cycles per iteration at 360 MHz -> about 1.2 ms (errs long)
HB_PAGE, HB_COL, HB_LEN = 0, 112, 12

CMD, DELAY, RESET, END = 0, 1, 2, 0xFF


def init_script():
    s = [(DELAY, 2), (RESET, 0), (DELAY, 30), (RESET, 1), (DELAY, 100),
         (RESET, 0), (DELAY, 30), (RESET, 1), (DELAY, 100), (DELAY, 100)]
    s += [(CMD, c) for c in (0xAE, 0xA2, 0xA0, 0xC0, 0x26, 0x81, 0x0D, 0xA6, 0xA4, 0x40, 0xB0, 0x10, 0x00, 0x2C)]
    for c in (0x2D, 0x2F, 0x2C, 0x2D, 0x2F, 0x2C, 0x2D, 0x2F, 0x2C, 0x2D, 0x2F):
        s += [(DELAY, 10), (CMD, c)]
    s += [(DELAY, 10), (CMD, 0x81), (CMD, 0x0D), (CMD, 0xAF)]
    return s


# ---------------------------------------------------------------- test pattern (original 5x7 glyphs)
GLYPHS = {
    'R': ['1111.', '1...1', '1...1', '1111.', '1.1..', '1..1.', '1...1'],
    '8': ['.111.', '1...1', '1...1', '.111.', '1...1', '1...1', '.111.'],
    'S': ['.1111', '1....', '1....', '.111.', '....1', '....1', '1111.'],
    'T': ['11111', '..1..', '..1..', '..1..', '..1..', '..1..', '..1..'],
    'A': ['.111.', '1...1', '1...1', '11111', '1...1', '1...1', '1...1'],
    'N': ['1...1', '11..1', '1.1.1', '1..11', '1...1', '1...1', '1...1'],
    'D': ['1111.', '1...1', '1...1', '1...1', '1...1', '1...1', '1111.'],
    'L': ['1....', '1....', '1....', '1....', '1....', '1....', '11111'],
    'O': ['.111.', '1...1', '1...1', '1...1', '1...1', '1...1', '.111.'],
    'E': ['11111', '1....', '1....', '1111.', '1....', '1....', '11111'],
    'P': ['1111.', '1...1', '1...1', '1111.', '1....', '1....', '1....'],
    'U': ['1...1', '1...1', '1...1', '1...1', '1...1', '1...1', '.111.'],
    'C': ['.1111', '1....', '1....', '1....', '1....', '1....', '.1111'],
    ' ': ['.....'] * 7,
}


def pattern():
    px = [[0] * 128 for _ in range(64)]
    for x in range(128): px[0][x] = px[63][x] = 1                 # border
    for y in range(64): px[y][0] = px[y][127] = 1
    for i in range(6):                                            # top-left corner triangle (orientation)
        for j in range(6 - i): px[2 + i][2 + j] = 1

    def text(s, x, y):
        for ch in s:
            g = GLYPHS[ch]
            for r in range(7):
                for c in range(5):
                    if g[r][c] == '1': px[y + r][x + c] = 1
            x += 6
    text('R8 STANDALONE', 12, 4)
    text('TEST PATTERN', 12, 14)
    for y in range(26, 58):                                       # checkerboard, 2x2 cells
        for x in range(4, 60):
            px[y][x] = ((x // 2) + (y // 2)) & 1
    for k in range(8):                                            # staircase: heights 4..32, left low
        h = 4 * (k + 1)
        for y in range(58 - h, 58):
            for x in range(66 + 7 * k, 66 + 7 * k + 5): px[y][x] = 1
    return px


def to_frame(px):
    fb = bytearray(1024)
    for b in range(8):
        for c in range(128):
            fb[b * 128 + c] = sum(px[b * 8 + k][c] << k for k in range(8))
    return bytes(fb)


def heartbeat(fb):
    on = bytearray(fb[HB_PAGE * 128 + HB_COL: HB_PAGE * 128 + HB_COL + HB_LEN])
    for i in range(2, HB_LEN - 2):
        on[i] |= 0x7C                                              # rows 2..6 of band 0
    return bytes(fb[HB_PAGE * 128 + HB_COL: HB_PAGE * 128 + HB_COL + HB_LEN]), bytes(on)


# ---------------------------------------------------------------- program
def build(loops_per_ms=LOOPS_PER_MS):
    a = asm6x.Asm(BASE)
    k = lambda v, r: (a.mvkl(v, r), a.mvkh(v, r))
    ret = lambda: (a.raw(RET_B3), a.nop(5))
    def poke(addr, val):
        k(addr, 'A3'); k(val, 'A0'); a.stw('A0', 'A3', 0)

    fb = to_frame(pattern())
    hb_off, hb_on = heartbeat(fb)
    script = b''.join(bytes(t) for t in init_script()) + bytes([END, 0])

    # ---- entry
    a.label('entry')
    a.mvc_from('CSR', 'B0'); a.nop(1)
    k(0xFFFFFFFE, 'B1'); a.l('and', 'B0', 'B1', 'B0')
    a.mvc_to('B0', 'CSR')                                       # GIE = 0
    k(0, 'B0'); a.mvc_to('B0', 'IER')
    k(0xFFF0, 'B0'); a.mvc_to('B0', 'ICR')
    k(STACK_TOP, 'B15')
    for lpsc in (3, 10):
        k(lpsc, 'A4'); a.callp('psc_on')
    poke(SYSCFG0 + 0x38, 0x83E70B13); poke(SYSCFG0 + 0x3C, 0x95A4F1E0)
    for i, v in sorted(PINMUX.items()):
        poke(SYSCFG0 + 0x120 + 4 * i, v)
    poke(SET23, CS_BIT); poke(SET8, A0_BIT | RST_BIT)            # all high before becoming outputs
    for reg, mask in ((DIR23, CS_BIT), (DIR8, A0_BIT | RST_BIT)):
        k(reg, 'A3'); a.ldw('A3', 0, 'A0'); a.nop(4)
        k(0xFFFFFFFF ^ mask, 'A5'); a.l('and', 'A0', 'A5', 'A0'); a.stw('A0', 'A3', 0)
    for off, v in ((0x00, 0), (0x00, 1), (0x04, 3), (0x14, 0x0E01), (0x08, 0), (0x0C, 0),
                   (0x48, 0x03030000), (0x4C, 0), (0x50, (17 << 8) | (1 << 17) | 8), (0x3C, 0),
                   (0x04, 0x01000003)):
        poke(SPI1 + off, v)
    a.label('run_script')
    k(0, 'A8')                                                  # patched below to the script address
    script_mvk_index = len(a.items) - 2
    a.label('sc_loop')
    a.ldst2('ldbu', 'A8', 1, 'A9', 'post++'); a.ldst2('ldbu', 'A8', 1, 'A4', 'post++'); a.nop(4)
    k(CMD, 'A0'); a.l('sub', 'A9', 'A0', 'A1')
    a.b('sc_not_cmd', 'A1'); a.nop(5)
    a.callp('lcd_cmd'); a.b('sc_loop'); a.nop(5)
    a.label('sc_not_cmd')
    k(DELAY, 'A0'); a.l('sub', 'A9', 'A0', 'A1')
    a.b('sc_not_delay', 'A1'); a.nop(5)
    a.callp('delay_ms'); a.b('sc_loop'); a.nop(5)
    a.label('sc_not_delay')
    k(RESET, 'A0'); a.l('sub', 'A9', 'A0', 'A1')
    a.b('sc_done', 'A1'); a.nop(5)                               # END (or anything unknown) stops
    a.l('add_cst', 0, 'A4', 'A2')
    a.b('rst_hi', 'A2'); a.nop(5)
    poke(CLR8, RST_BIT); a.b('sc_loop'); a.nop(5)
    a.label('rst_hi')
    poke(SET8, RST_BIT); a.b('sc_loop'); a.nop(5)
    a.label('sc_done')
    # ---- frame: 8 bands
    k(0, 'B2')                                                  # B2 = band
    a.label('fr_loop')
    k(0xB0, 'A4'); a.l('add', 'A4', 'B2', 'A4')
    k(0x00, 'A6'); k(0x10, 'B6')
    k(0, 'B4'); fr_mvk_index = len(a.items) - 2                 # patched: FRAME address
    a.mvkl(128, 'A5'); a.mvkh(0, 'A5')
    # B4 += band*128: add 128 per band via a running pointer kept on the stack
    a.stw('B2', 'B15', 0)
    a.l('add_cst', 0, 'B2', 'B1')
    a.label('fr_mul')
    a.b('fr_mul_done', '!B1'); a.nop(5)
    a.addk(128, 'B4'); a.addk(-1, 'B1'); a.b('fr_mul'); a.nop(5)
    a.label('fr_mul_done')
    a.callp('send_seg')
    a.ldw('B15', 0, 'B2'); a.nop(4)
    a.addk(1, 'B2')
    k(8, 'A0'); a.l('sub', 'A0', 'B2', 'A1')
    a.b('fr_loop', 'A1'); a.nop(5)
    # ---- heartbeat forever
    a.label('hb_loop')
    for which in ('on', 'off'):
        k(0xB0 + HB_PAGE, 'A4'); k(HB_COL & 0xF, 'A6'); k(0x10 | (HB_COL >> 4), 'B6')
        k(0, 'B4'); idx = len(a.items) - 2
        a.mvkl(HB_LEN, 'A5'); a.mvkh(0, 'A5')
        a.callp('send_seg')
        k(500, 'A4'); a.callp('delay_ms')
        if which == 'on': hb_on_idx = idx
        else: hb_off_idx = idx
    a.b('hb_loop'); a.nop(5)

    # ---- psc_on(A4 = LPSC): leaf
    a.label('psc_on')
    k(PSC1 + 0x128, 'A3')
    a.label('psc_w1'); a.ldw('A3', 0, 'A0'); a.nop(4)
    k(1, 'A5'); a.l('and', 'A0', 'A5', 'A1'); a.b('psc_w1', 'A1'); a.nop(5)
    k(PSC1 + 0xA00, 'A3'); a.l('add', 'A3', 'A4', 'A3'); a.l('add', 'A3', 'A4', 'A3')
    a.l('add', 'A3', 'A4', 'A3'); a.l('add', 'A3', 'A4', 'A3')  # A3 = MDCTL + 4*n
    a.ldw('A3', 0, 'A0'); a.nop(4)
    k(0xFFFFFFE0, 'A5'); a.l('and', 'A0', 'A5', 'A0'); a.l('add_cst', 3, 'A0', 'A0')
    a.stw('A0', 'A3', 0)
    k(PSC1 + 0x120, 'A3'); k(1, 'A0'); a.stw('A0', 'A3', 0)
    k(PSC1 + 0x128, 'A3')
    a.label('psc_w2'); a.ldw('A3', 0, 'A0'); a.nop(4)
    k(1, 'A5'); a.l('and', 'A0', 'A5', 'A1'); a.b('psc_w2', 'A1'); a.nop(5)
    ret()

    # ---- spi_byte(A4 = byte): leaf; clobbers A0 A1 A3 A5
    a.label('spi_byte')
    k(SPI1 + 0x3C, 'A3'); a.stw('A4', 'A3', 0)
    k(SPI1 + 0x40, 'A3'); k(0x7FFFFFFF, 'A5')
    a.label('spi_w'); a.ldw('A3', 0, 'A0'); a.nop(4)
    a.l('cmpltu', 'A5', 'A0', 'A1')                              # RXEMPTY (bit 31) set -> wait
    a.b('spi_w', 'A1'); a.nop(5)
    ret()

    # ---- lcd_cmd(A4): CS low, A0 low, byte, A0 high, CS high; clobbers A0 A1 A3 A5
    a.label('lcd_cmd')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
    poke(CLR23, CS_BIT); poke(CLR8, A0_BIT)
    a.callp('spi_byte')
    poke(SET8, A0_BIT); poke(SET23, CS_BIT)
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15')
    ret()

    # ---- send_seg(A4 = page cmd, A6 = col-lo cmd, B6 = col-hi cmd, B4 = src, A5 = len)
    a.label('send_seg')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A6', 'B15', 8); a.stw('B6', 'B15', 12); a.stw('B4', 'B15', 16); a.stw('A5', 'B15', 20)
    a.callp('lcd_cmd')
    a.ldw('B15', 8, 'A4'); a.nop(4); a.callp('lcd_cmd')
    a.ldw('B15', 12, 'A4'); a.nop(4); a.callp('lcd_cmd')
    poke(CLR23, CS_BIT)                                         # A0 is high: data
    a.ldw('B15', 16, 'A8'); a.ldw('B15', 20, 'B1'); a.nop(4)
    a.label('ss_loop')
    a.ldst2('ldbu', 'A8', 1, 'A4', 'post++'); a.nop(4)
    a.callp('spi_byte')
    a.addk(-1, 'B1')
    a.b('ss_loop', 'B1'); a.nop(5)
    poke(SET23, CS_BIT)
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(32, 'B15')
    ret()

    # ---- delay_ms(A4): leaf; clobbers A1 B0
    a.label('delay_ms')
    a.l('add_cst', 0, 'A4', 'A1')
    a.b('dl_done', '!A1'); a.nop(5)
    a.label('dl_ms')
    k(loops_per_ms, 'B0')
    a.label('dl_in'); a.addk(-1, 'B0'); a.b('dl_in', 'B0'); a.nop(5)
    a.l('add_cst', -1, 'A4', 'A4'); a.l('add_cst', 0, 'A4', 'A1')
    a.b('dl_ms', 'A1'); a.nop(5)
    a.label('dl_done')
    ret()

    # ---- data after code
    code_len = len(a.assemble())
    SCRIPT = BASE + code_len
    FRAME = (SCRIPT + len(script) + 31) & ~31
    HB_ON = FRAME + 1024
    HB_OFF = HB_ON + 32
    for idx, val in ((script_mvk_index, SCRIPT), (fr_mvk_index, FRAME), (hb_on_idx, HB_ON), (hb_off_idx, HB_OFF)):
        a.items[idx] = ('mvk', val & 0xffff, a.items[idx][2], 0)
        a.items[idx + 1] = ('mvk', (val >> 16) & 0xffff, a.items[idx + 1][2], 1)
    code = a.assemble()
    assert len(code) == code_len
    blob = bytearray(code) + script
    blob += bytes(FRAME - BASE - len(blob)) + fb + hb_on + bytes(32 - HB_LEN) + hb_off + bytes(32 - HB_LEN)
    return bytes(blob), a, dict(SCRIPT=SCRIPT, FRAME=FRAME, HB_ON=HB_ON, HB_OFF=HB_OFF, fb=fb)


def make_image(stock, blob):
    d = bytearray(stock)
    cmds, _ = r8fw.parse_ais(d)
    assert [c[0] for c in cmds[:3]] == ['SeqReadEnable', 'FunctionExec', 'FunctionExec']
    keep_end = cmds[3][1]                                       # first SectionLoad of the stock script
    assert keep_end == 0x84
    ais = bytes(d[0x40:keep_end])
    ais += struct.pack('<III', 0x58535901, BASE, len(blob)) + blob + bytes((-len(blob)) % 4)
    ais += struct.pack('<II', 0x58535906, BASE)
    assert 0x40 + len(ais) < 0x12000, 'must not reach past the stock first section'
    d[0x40:0x40 + len(ais)] = ais
    struct.pack_into('<I', d, 0x34, r8fw.checksum(d))
    return bytes(d), len(ais)


def preview(fb, path, hb=None):
    from PIL import Image, ImageDraw
    S = 5
    img = Image.new('RGB', (128 * S + 20, 64 * S + 20), (205, 215, 190)); dr = ImageDraw.Draw(img)
    for b in range(8):
        for c in range(128):
            for kk in range(8):
                if fb[b * 128 + c] >> kk & 1:
                    x, y = 10 + c * S, 10 + (b * 8 + kk) * S
                    dr.rectangle([x, y, x + S - 1, y + S - 1], fill=(25, 35, 45))
    img.save(path)


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    stock = open(src, 'rb').read()
    assert hashlib.sha256(stock).hexdigest().startswith(STOCK_SHA), 'not stock v1.17'
    blob, a, info = build()
    out, ais_len = make_image(stock, blob)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    print(f'program+data {len(blob)} bytes at {BASE:#x}; new AIS script {ais_len} bytes; file size {len(out)} (same as stock)')
    print('sha256', hashlib.sha256(out).hexdigest())
    if len(sys.argv) > 3:
        fb = bytearray(info['fb'])
        _, on = heartbeat(info['fb']); fb[HB_PAGE * 128 + HB_COL:HB_PAGE * 128 + HB_COL + HB_LEN] = on
        preview(bytes(fb), sys.argv[3])
