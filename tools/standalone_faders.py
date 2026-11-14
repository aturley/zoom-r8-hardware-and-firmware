#!/usr/bin/env python3
"""Standalone fader display for the Zoom R8: our own LCD and panel-link drivers, no Zoom code after the boot loader.

Same image layout as standalone.py (stock header, stock PLL/DDR boot commands, one SectionLoad into L2, JumpClose).

Program:
  1. Interrupts off, stack in L2. PSC1 on: GPIO (3), SPI1 (10), UART2 (13). Stock pin mux (PINMUX4, which holds
     UART2's pins and is board-revision dependent, is left as the boot loader set it). LCD pins and SPI1 as in
     standalone.py.
  2. LCD init script (stock sequence), then an initial screen: title + "RX"/"FD" indicator boxes in band 0,
     nine 1-pixel baselines in bands 1-7.
  3. UART2 (0x01D0D000) as stock opens it: reset, DLL 67 / DLH 0 (180 MHz / 16 / 67 = 167.9 kbaud for the stock
     166,666 setting), MDR 0 (16x), FCR 0 -> 1 -> 0x0F, IER 0 (polled), MCR 0, LCR 3 (8N1), PWREMU 0x6001.
  4. Panel start-up: replay the bytes the stock firmware sent at boot (captured on the wire):
     84 01 01 01 F7, A0 01 01 02 F7, 9E 01 00 F7, 81 01 00 F7, 83 01 00 F7, (200 ms), 8A 01 01 02 F7, 8B 01 01 00 F7.
  5. Main loop: drain UART2 (also polled between every SPI byte while drawing). Panel->DSP framing: a byte >= 0x80
     is a status byte; data bytes follow. Status 0xC3..0xCB = faders 1-8 and MASTER, value 0..127.
     Any received byte fills the "RX" box; any fader message fills the "FD" box; a fader change redraws the bars.

Usage: python3 standalone_faders.py STOCK_R8SYS.BIN OUT/R8SYS.BIN [preview.png]
"""
import os, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import asm6x
import standalone as SA
from standalone import (BASE, STACK_TOP, RET_B3, PSC1, SYSCFG0, SET23, CLR23, DIR23, SET8, CLR8, DIR8,
                        CS_BIT, A0_BIT, RST_BIT, SPI1, PINMUX, LOOPS_PER_MS, CMD, DELAY, RESET, END, GLYPHS,
                        init_script, make_image, preview)
from faderbar import STOCK_SHA

UART2 = 0x01D0D000
TX = 3
NSLOT = 9
FIRST_FADER = 0xC3
BAR_W, PITCH, X0 = 8, 12, 12
TOP_FULL = 10                      # value 127 reaches row 10; value 0 leaves the bottom row (63)
PANEL_START = [0x84, 0x01, 0x01, 0x01, 0xF7, 0xA0, 0x01, 0x01, 0x02, 0xF7, 0x9E, 0x01, 0x00, 0xF7,
               0x81, 0x01, 0x00, 0xF7, 0x83, 0x01, 0x00, 0xF7]
PANEL_LATER = [0x8A, 0x01, 0x01, 0x02, 0xF7, 0x8B, 0x01, 0x01, 0x00, 0xF7]
RX_BOX, FD_BOX = (92, 99), (117, 124)
GLYPHS = dict(GLYPHS, F=['11111', '1....', '1....', '1111.', '1....', '1....', '1....'],
              X=['1...1', '1...1', '.1.1.', '..1..', '.1.1.', '1...1', '1...1'])


def top_of(v):
    return 63 - (v * (63 - TOP_FULL)) // 127


def tables():
    toptab = bytes(top_of(v) for v in range(128))
    mask = bytearray()
    for e in range(121):
        d = e - 56
        mask.append(0xFF if d <= 0 else 0 if d >= 8 else (0xFF << d) & 0xFF)
    colmap = bytearray([NSLOT] * 128)
    for s in range(NSLOT):
        for c in range(X0 + PITCH * s, X0 + PITCH * s + BAR_W): colmap[c] = s
    tops = bytes([63] * NSLOT + [64])                         # slot 9 = empty columns (always blank)
    return toptab, bytes(mask), bytes(colmap), tops


def band0_variants():
    out = []
    for idx in range(4):
        rx, fd = idx & 1, idx >> 1
        px = [[0] * 128 for _ in range(8)]

        def text(s, x):
            for ch in s:
                g = GLYPHS[ch]
                for r in range(7):
                    for c in range(5):
                        if g[r][c] == '1': px[r][x + c] = 1
                x += 6
        text('R8 FADERS', 2)
        text('RX', 79)
        text('FD', 104)
        for (x0, x1), on in ((RX_BOX, rx), (FD_BOX, fd)):
            for x in range(x0, x1 + 1):
                for y in range(0, 7):
                    if on or y in (0, 6) or x in (x0, x1): px[y][x] = 1
        out.append(bytes(sum(px[k][c] << k for k in range(8)) for c in range(128)))
    return out


def model_frame(values, idx):
    """Reference 1 KB frame for given fader values (None = never moved) and indicator index."""
    fb = bytearray(1024)
    fb[0:128] = band0_variants()[idx]
    for s in range(NSLOT):
        top = 63 if values[s] is None else top_of(values[s])
        for c in range(X0 + PITCH * s, X0 + PITCH * s + BAR_W):
            for r in range(max(top, 8), 64):
                fb[(r // 8) * 128 + c] |= 1 << (r % 8)
    return bytes(fb)


def build(loops_per_ms=LOOPS_PER_MS):
    a = asm6x.Asm(BASE)
    refs = []
    k = lambda v, r: (a.mvkl(v, r), a.mvkh(v, r))
    def ref(name, r):
        refs.append((len(a.items), name)); k(0, r)
    ret = lambda: (a.raw(RET_B3), a.nop(5))
    def poke(addr, val):
        k(addr, 'A3'); k(val, 'A0'); a.stw('A0', 'A3', 0)
    def setvar(name, val):
        ref(name, 'A3'); k(val, 'A0'); a.stw('A0', 'A3', 0)

    # ---------------- entry and hardware bring-up
    a.label('entry')
    a.mvc_from('CSR', 'B0'); a.nop(1)
    k(0xFFFFFFFE, 'B1'); a.l('and', 'B0', 'B1', 'B0'); a.mvc_to('B0', 'CSR')
    k(0, 'B0'); a.mvc_to('B0', 'IER')
    k(0xFFF0, 'B0'); a.mvc_to('B0', 'ICR')
    k(STACK_TOP, 'B15')
    for lpsc in (3, 10, 13):
        k(lpsc, 'A4'); a.callp('psc_on')
    poke(SYSCFG0 + 0x38, 0x83E70B13); poke(SYSCFG0 + 0x3C, 0x95A4F1E0)
    for i, v in sorted(PINMUX.items()):
        poke(SYSCFG0 + 0x120 + 4 * i, v)
    poke(SET23, CS_BIT); poke(SET8, A0_BIT | RST_BIT)
    for reg, mask in ((DIR23, CS_BIT), (DIR8, A0_BIT | RST_BIT)):
        k(reg, 'A3'); a.ldw('A3', 0, 'A0'); a.nop(4)
        k(0xFFFFFFFF ^ mask, 'A5'); a.l('and', 'A0', 'A5', 'A0'); a.stw('A0', 'A3', 0)
    for off, v in ((0x00, 0), (0x00, 1), (0x04, 3), (0x14, 0x0E01), (0x08, 0), (0x0C, 0),
                   (0x48, 0x03030000), (0x4C, 0), (0x50, (17 << 8) | (1 << 17) | 8), (0x3C, 0),
                   (0x04, 0x01000003)):
        poke(SPI1 + off, v)
    ref('SCRIPT_LCD', 'A8'); a.callp('run_script')
    setvar('DIRTY', 1); setvar('SHOWN', 0xFF)
    a.callp('redraw')                                          # initial screen (baselines, empty boxes)
    a.callp('show_ind')
    # UART2, as stock opens it (polled)
    for off, v in ((0x30, 0), (0x20, 67), (0x24, 0), (0x34, 0), (0x08, 0), (0x08, 1), (0x08, 0x0F),
                   (0x04, 0), (0x10, 0), (0x0C, 3), (0x30, 0x6001)):
        poke(UART2 + off, v)
    ref('SCRIPT_PANEL', 'A8'); a.callp('run_script')

    # ---------------- main loop
    a.label('main')
    a.callp('rx_poll')
    ref('DIRTY', 'A3'); a.ldw('A3', 0, 'A1'); a.nop(4)
    a.b('m_ind', '!A1'); a.nop(5)
    a.callp('redraw')
    a.label('m_ind')
    a.callp('show_ind')
    a.b('main'); a.nop(5)

    # ---------------- redraw: DIRTY = 0, render bands 1..7 into FRAME, send them
    a.label('redraw')
    a.addk(-16, 'B15'); a.stw('B3', 'B15', 4)
    setvar('DIRTY', 0)
    ref('FRAME', 'A7'); a.addk(128, 'A7')
    ref('TOPS', 'A6'); ref('MASKTAB', 'A9')
    k(7, 'B2'); k(48, 'B5')                                    # bias = 56 - 8*band, band 1 first
    a.label('rd_band')
    ref('COLMAP', 'A8'); k(128, 'B1')
    a.label('rd_col')
    a.ldst2('ldbu', 'A8', 1, 'A3', 'post++'); a.nop(4)          # slot
    a.l('add', 'A3', 'A6', 'A3')
    a.ldst2('ldbu', 'A3', 0, 'A3'); a.nop(4)                   # top
    a.l('add', 'A3', 'B5', 'A3'); a.l('add', 'A3', 'A9', 'A3')
    a.ldst2('ldbu', 'A3', 0, 'A0'); a.nop(4)
    a.ldst2('stb', 'A7', 1, 'A0', 'post++')
    a.addk(-1, 'B1'); a.b('rd_col', 'B1'); a.nop(5)
    a.addk(-8, 'B5'); a.addk(-1, 'B2'); a.b('rd_band', 'B2'); a.nop(5)
    k(1, 'B2')                                                 # send bands 1..7
    a.label('rd_send')
    a.stw('B2', 'B15', 8)
    k(0xB0, 'A4'); a.l('add', 'A4', 'B2', 'A4')
    k(0x00, 'A6'); k(0x10, 'B6')
    ref('FRAME', 'B4')
    a.l('add_cst', 0, 'B2', 'B1')
    a.label('rd_mul'); a.b('rd_mul_done', '!B1'); a.nop(5)
    a.addk(128, 'B4'); a.addk(-1, 'B1'); a.b('rd_mul'); a.nop(5)
    a.label('rd_mul_done')
    k(128, 'A5')
    a.callp('send_seg')
    a.ldw('B15', 8, 'B2'); a.nop(4)
    a.addk(1, 'B2')
    k(8, 'A0'); a.l('sub', 'A0', 'B2', 'A1')
    a.b('rd_send', 'A1'); a.nop(5)
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(16, 'B15')
    ret()

    # ---------------- show_ind: idx = RXSEEN + 2*FDSEEN; if idx != SHOWN, send that band-0 variant
    a.label('show_ind')
    a.addk(-16, 'B15'); a.stw('B3', 'B15', 4)
    ref('RXSEEN', 'A3'); a.ldw('A3', 0, 'A0'); ref('FDSEEN', 'A3'); a.ldw('A3', 0, 'A5'); a.nop(4)
    a.l('add', 'A0', 'A5', 'A0'); a.l('add', 'A0', 'A5', 'A0')  # idx
    ref('SHOWN', 'A3'); a.ldw('A3', 0, 'A5'); a.nop(4)
    a.l('sub', 'A0', 'A5', 'A1')
    a.b('si_done', '!A1'); a.nop(5)
    a.stw('A0', 'A3', 0)
    ref('BAND0', 'B4')
    a.l('add_cst', 0, 'A0', 'A1')
    a.label('si_mul'); a.b('si_mul_done', '!A1'); a.nop(5)
    a.addk(128, 'B4'); a.l('add_cst', -1, 'A1', 'A1'); a.b('si_mul'); a.nop(5)
    a.label('si_mul_done')
    k(0xB0, 'A4'); k(0x00, 'A6'); k(0x10, 'B6'); k(128, 'A5')
    a.callp('send_seg')
    a.label('si_done')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(16, 'B15')
    ret()

    # ---------------- rx_poll: drain UART2 and parse. Leaf. Clobbers A0 A1 A2 A3 A5 A7 A9 B0
    a.label('rx_poll')
    a.label('rp_loop')
    k(UART2 + 0x14, 'A3'); a.ldw('A3', 0, 'A0'); a.nop(4)
    k(1, 'A5'); a.l('and', 'A0', 'A5', 'A1')                   # LSR.DR
    a.b('rp_done', '!A1'); a.nop(5)
    k(UART2, 'A7'); a.ldw('A7', 0, 'A9'); a.nop(4)
    k(0xFF, 'A5'); a.l('and', 'A9', 'A5', 'A9')                # A9 = byte
    setvar('RXSEEN', 1)
    k(0x7F, 'A0'); a.l('cmpltu', 'A0', 'A9', 'A1')
    a.b('rp_data', '!A1'); a.nop(5)
    ref('STATE', 'A3'); a.stw('A9', 'A3', 0)
    a.b('rp_loop'); a.nop(5)
    a.label('rp_data')
    ref('STATE', 'A3'); a.ldw('A3', 0, 'A0'); a.nop(4)
    k(FIRST_FADER, 'A5'); a.l('sub', 'A0', 'A5', 'A2')          # slot
    k(NSLOT, 'A5'); a.l('cmpltu', 'A2', 'A5', 'A1')
    a.b('rp_loop', '!A1'); a.nop(5)
    ref('TOPTAB', 'A3'); a.l('add', 'A3', 'A9', 'A3'); a.ldst2('ldbu', 'A3', 0, 'A0'); a.nop(4)
    ref('TOPS', 'A3'); a.l('add', 'A3', 'A2', 'A3'); a.ldst2('stb', 'A3', 0, 'A0')
    setvar('DIRTY', 1); setvar('FDSEEN', 1)
    a.b('rp_loop'); a.nop(5)
    a.label('rp_done')
    ret()

    # ---------------- run_script(A8 = script): (op, arg) pairs; op CMD / DELAY / RESET / TX / END
    a.label('run_script')
    a.addk(-16, 'B15'); a.stw('B3', 'B15', 4)
    a.label('sc_loop')
    a.ldst2('ldbu', 'A8', 1, 'A9', 'post++'); a.ldst2('ldbu', 'A8', 1, 'A4', 'post++'); a.nop(4)
    a.stw('A8', 'B15', 8)
    for op, lab in ((CMD, 'lcd_cmd'), (DELAY, 'delay_ms'), (TX, 'tx_byte')):
        k(op, 'A0'); a.l('sub', 'A9', 'A0', 'A1')
        a.b(f'sc_not{op}', 'A1'); a.nop(5)
        a.callp(lab); a.b('sc_next'); a.nop(5)
        a.label(f'sc_not{op}')
    k(RESET, 'A0'); a.l('sub', 'A9', 'A0', 'A1')
    a.b('sc_done', 'A1'); a.nop(5)
    a.l('add_cst', 0, 'A4', 'A2')
    a.b('rst_hi', 'A2'); a.nop(5)
    poke(CLR8, RST_BIT); a.b('sc_next'); a.nop(5)
    a.label('rst_hi')
    poke(SET8, RST_BIT)
    a.label('sc_next')
    a.ldw('B15', 8, 'A8'); a.nop(4)
    a.b('sc_loop'); a.nop(5)
    a.label('sc_done')
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(16, 'B15')
    ret()

    # ---------------- tx_byte(A4): wait THRE, write THR. Leaf. Clobbers A0 A1 A3 A5
    a.label('tx_byte')
    k(UART2 + 0x14, 'A3'); k(0x20, 'A5')
    a.label('tx_w'); a.ldw('A3', 0, 'A0'); a.nop(4)
    a.l('and', 'A0', 'A5', 'A1'); a.b('tx_w', '!A1'); a.nop(5)
    k(UART2, 'A3'); a.stw('A4', 'A3', 0)
    ret()

    # ---------------- psc_on(A4 = LPSC). Leaf.
    a.label('psc_on')
    k(PSC1 + 0x128, 'A3')
    a.label('psc_w1'); a.ldw('A3', 0, 'A0'); a.nop(4)
    k(1, 'A5'); a.l('and', 'A0', 'A5', 'A1'); a.b('psc_w1', 'A1'); a.nop(5)
    k(PSC1 + 0xA00, 'A3')
    for _ in range(4): a.l('add', 'A3', 'A4', 'A3')
    a.ldw('A3', 0, 'A0'); a.nop(4)
    k(0xFFFFFFE0, 'A5'); a.l('and', 'A0', 'A5', 'A0'); a.l('add_cst', 3, 'A0', 'A0')
    a.stw('A0', 'A3', 0)
    k(PSC1 + 0x120, 'A3'); k(1, 'A0'); a.stw('A0', 'A3', 0)
    k(PSC1 + 0x128, 'A3')
    a.label('psc_w2'); a.ldw('A3', 0, 'A0'); a.nop(4)
    k(1, 'A5'); a.l('and', 'A0', 'A5', 'A1'); a.b('psc_w2', 'A1'); a.nop(5)
    ret()

    # ---------------- spi_byte(A4). Leaf. Clobbers A0 A1 A3 A5
    a.label('spi_byte')
    k(SPI1 + 0x3C, 'A3'); a.stw('A4', 'A3', 0)
    k(SPI1 + 0x40, 'A3'); k(0x7FFFFFFF, 'A5')
    a.label('spi_w'); a.ldw('A3', 0, 'A0'); a.nop(4)
    a.l('cmpltu', 'A5', 'A0', 'A1'); a.b('spi_w', 'A1'); a.nop(5)
    ret()

    # ---------------- lcd_cmd(A4)
    a.label('lcd_cmd')
    a.addk(-8, 'B15'); a.stw('B3', 'B15', 4)
    poke(CLR23, CS_BIT); poke(CLR8, A0_BIT)
    a.callp('spi_byte')
    poke(SET8, A0_BIT); poke(SET23, CS_BIT)
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(8, 'B15')
    ret()

    # ---------------- send_seg(A4 = page cmd, A6 = col-lo, B6 = col-hi, B4 = src, A5 = len); polls RX per byte
    a.label('send_seg')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A6', 'B15', 8); a.stw('B6', 'B15', 12); a.stw('B4', 'B15', 16); a.stw('A5', 'B15', 20)
    a.callp('lcd_cmd')
    a.ldw('B15', 8, 'A4'); a.nop(4); a.callp('lcd_cmd')
    a.ldw('B15', 12, 'A4'); a.nop(4); a.callp('lcd_cmd')
    poke(CLR23, CS_BIT)
    a.ldw('B15', 16, 'A8'); a.ldw('B15', 20, 'B1'); a.nop(4)
    a.label('ss_loop')
    a.ldst2('ldbu', 'A8', 1, 'A4', 'post++'); a.nop(4)
    a.callp('spi_byte')
    a.callp('rx_poll')
    a.addk(-1, 'B1'); a.b('ss_loop', 'B1'); a.nop(5)
    poke(SET23, CS_BIT)
    a.ldw('B15', 4, 'B3'); a.nop(4); a.addk(32, 'B15')
    ret()

    # ---------------- delay_ms(A4). Leaf. Clobbers A1 A4 B0
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

    # ---------------- data layout
    code_len = len(a.assemble())
    toptab, mask, colmap, tops = tables()
    lcd = b''.join(bytes(t) for t in init_script()) + bytes([END, 0])
    panel = b''.join(bytes([TX, b]) for b in PANEL_START) + bytes([DELAY, 200]) + \
        b''.join(bytes([TX, b]) for b in PANEL_LATER) + bytes([END, 0])
    items = [('SCRIPT_LCD', lcd), ('SCRIPT_PANEL', panel), ('STATE', bytes(4)), ('DIRTY', bytes(4)),
             ('RXSEEN', bytes(4)), ('FDSEEN', bytes(4)), ('SHOWN', bytes(4)), ('TOPS', tops),
             ('COLMAP', colmap), ('TOPTAB', toptab), ('MASKTAB', mask), ('BAND0', b''.join(band0_variants())),
             ('FRAME', bytes(1024))]
    addr = {}
    data = bytearray()
    pos = BASE + code_len
    for name, blob in items:
        pad = (-pos) % 32
        data += bytes(pad); pos += pad
        addr[name] = pos
        data += blob; pos += len(blob)
    for idx, name in refs:
        v = addr[name]
        a.items[idx] = ('mvk', v & 0xffff, a.items[idx][2], 0)
        a.items[idx + 1] = ('mvk', (v >> 16) & 0xffff, a.items[idx + 1][2], 1)
    code = a.assemble()
    assert len(code) == code_len
    blob = code + bytes(data)
    assert BASE + len(blob) < STACK_TOP - 0x1000, 'program would reach the stack'
    return blob, a, addr


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    stock = open(src, 'rb').read()
    assert hashlib.sha256(stock).hexdigest().startswith(STOCK_SHA), 'not stock v1.17'
    blob, a, addr = build()
    out, ais_len = make_image(stock, blob)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    print(f'program+data {len(blob)} bytes at {BASE:#x}; AIS {ais_len} bytes; file size {len(out)} (same as stock)')
    print('sha256', hashlib.sha256(out).hexdigest())
    if len(sys.argv) > 3:
        preview(model_frame([0, 20, 40, 60, 80, 100, 127, None, 64], 3), sys.argv[3])
