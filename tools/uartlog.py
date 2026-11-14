#!/usr/bin/env python3
"""Build the UART2 (DSP <-> panel sub-MCU) logger patch for Zoom R8 firmware v1.17.

Patches (all other bytes stock):
  * TX call sites 0xC07BB748 and 0xC07C8CE0: CALLP uart_write -> CALLP tx_hook
  * RX call site 0xC07BBCF8:                 CALLP uart_read  -> CALLP rx_hook
  * VERSION menu action 0xC06E24EC:          0xC07ED7CC       -> flush_hook
Code + data live in the XDC Text name table (0xC06D9800-0xC06D9DE0).

Log ring: 0xC0050000-0xC0250000 (2 MB, used only by the BOOT updater). Record format (byte-packed):
  A5 | dir ('T' DSP->panel, 'R' panel->DSP) | len lo | len hi | TSCL (4 bytes LE) | TSCH (4 bytes LE) | data
Opening VERSION writes A:\\UARTLOG.BIN = 16-byte header ('R8UL', version, log start, byte count) + records,
then clears the log and shows the normal VERSION screen.
"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(__file__))
import asm6x

UART_WRITE, UART_READ = 0xc07d5350, 0xc07d54a8
OPEN, WRITE, CLOSE = 0xc0763c6c, 0xc0762db8, 0xc0765c6c
ORIG_VERSION = 0xc07ed7cc
TX_SITES = [0xc07bb748, 0xc07c8ce0]
RX_SITE = 0xc07bbcf8
HOOK_SLOT = 0xc06e24ec
CAVE, CAVE_END = 0xc06d9800, 0xc06d9de0
DATA = 0xc06d9d00
LOG_START, LOG_END = 0xc0050000, 0xc0250000
CHUNK = 0x4000             # fits a sign-extended MVK, so one predicated MVK sets it
RET_B3 = 0x008ca362          # firmware's own "B .S2 B3" word (1568 occurrences)

IDX, STARTED, SCR, FD, WR, HDR, PATH = DATA, DATA + 4, DATA + 8, DATA + 20, DATA + 24, DATA + 32, DATA + 48


def log_core(a, dirch, out):
    """Append one record. In: B4 = len, A6 = buf. Clobbers A0 A1 A2 A3 A5 A7 A9 B0 B1 B5 B9."""
    a.mvk32(IDX, 'A3')
    a.ldw('A3', 0, 'A5'); a.nop(4)                      # A5 = pos
    a.l('add', 'A5', 'B4', 'A7')                        # A7 = pos + len
    a.addk(12, 'A7')                                    # A7 = end of record (12-byte header)
    a.mvk32(LOG_END, 'A9')
    a.l('cmpltu', 'A9', 'A7', 'A1')                     # LOG_END < end -> full
    a.b(out, 'A1'); a.nop(5)
    a.l('cmpltu', 'A7', 'A5', 'A1')                     # wrapped (absurd len) -> skip
    a.b(out, 'A1'); a.nop(5)
    a.stw('A7', 'A3', 0)                                # reserve
    a.mvkl(0xA5, 'A0'); a.ldst2('stb', 'A5', 1, 'A0', 'post++')
    a.mvkl(ord(dirch), 'A0'); a.ldst2('stb', 'A5', 1, 'A0', 'post++')
    a.mvk32(SCR, 'A2')
    a.ldst2('stw', 'A2', 0, 'B4')                       # SCR[0] = len
    for k in (0, 1):
        a.ldst2('ldbu', 'A2', k, 'A0'); a.nop(4); a.ldst2('stb', 'A5', 1, 'A0', 'post++')
    a.ldw('A3', 4, 'A1'); a.nop(4)                      # STARTED?
    a.mvc_to('B0', 'TSCL', '!A1')                       # first use: start the time-stamp counter
    a.mvkl(1, 'A0'); a.stw('A0', 'A3', 4)
    a.mvc_from('TSCL', 'B5'); a.nop(1)                  # reading TSCL latches TSCH
    a.mvc_from('TSCH', 'B9'); a.nop(1)
    a.ldst2('stw', 'A2', 1, 'B5')                       # SCR[1] = TSCL
    a.ldst2('stw', 'A2', 2, 'B9')                       # SCR[2] = TSCH
    for k in (4, 5, 6, 7, 8, 9, 10, 11):
        a.ldst2('ldbu', 'A2', k, 'A0'); a.nop(4); a.ldst2('stb', 'A5', 1, 'A0', 'post++')
    a.l('add_cst', 0, 'A6', 'A7')                       # A7 = buf
    a.l('add_cst', 0, 'B4', 'B1')                       # B1 = count (>0, checked by caller)
    lp = out + '_copy'
    a.label(lp)
    a.ldst2('ldbu', 'A7', 1, 'A0', 'post++'); a.nop(4)
    a.ldst2('stb', 'A5', 1, 'A0', 'post++')
    a.addk(-1, 'B1')
    a.b(lp, 'B1'); a.nop(5)


def build():
    a = asm6x.Asm(CAVE)
    # ---------------- TX hook: log then tail-jump to uart_write (A4,B4,A6,B6,B3 untouched) -------------
    a.label('tx_hook')
    a.l('add_cst', 0, 'B4', 'B1')
    a.b('tx_out', '!B1'); a.nop(5)
    log_core(a, 'T', 'tx_out')
    a.label('tx_out')
    a.b(UART_WRITE); a.nop(5)
    # ---------------- RX hook: call uart_read, then log what it returned -----------------------------
    a.label('rx_hook')
    a.addk(-24, 'B15')
    a.stw('B3', 'B15', 4); a.stw('A6', 'B15', 8); a.stw('B6', 'B15', 12)
    a.callp(UART_READ)
    a.stw('A4', 'B15', 16)
    a.ldw('B15', 12, 'A3'); a.nop(4)
    a.ldst2('ldw', 'A3', 0, 'B4'); a.nop(4)              # B4 = bytes read
    a.ldw('B15', 8, 'A6'); a.nop(4)                     # A6 = buf
    a.l('add_cst', 0, 'B4', 'B1')
    a.b('rx_out', '!B1'); a.nop(5)
    log_core(a, 'R', 'rx_out')
    a.label('rx_out')
    a.ldw('B15', 16, 'A4'); a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(24, 'B15')
    a.raw(RET_B3); a.nop(5)
    # ---------------- flush hook (VERSION): write header + log to A:\UARTLOG.BIN, clear, continue ----
    a.label('flush_hook')
    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4)
    a.mvk32(IDX, 'A3'); a.ldw('A3', 0, 'A4'); a.nop(4)  # A4 = end snapshot
    a.mvk32(LOG_START, 'A5')
    a.l('sub', 'A4', 'A5', 'A7')                        # A7 = bytes = end - start
    a.mvk32(HDR, 'A2'); a.stw('A7', 'A2', 12)           # header.bytes
    a.stw('A5', 'B15', 8)                               # [2] ptr = LOG_START
    a.stw('A7', 'B15', 12)                              # [3] remaining
    a.mvk32(FD, 'A4'); a.mvk32(PATH, 'B4'); a.mvkl(0x101, 'A6'); a.mvkh(0, 'A6'); a.mvkl(0x80, 'B6'); a.mvkh(0, 'B6')
    a.callp(OPEN)
    a.stw('A4', 'B15', 16); a.ldw('B15', 16, 'A1'); a.nop(4)
    a.b('fl_done', 'A1'); a.nop(5)
    a.mvk32(FD, 'A3'); a.ldw('A3', 0, 'A4'); a.nop(4)
    a.mvk32(HDR, 'B4'); a.mvkl(16, 'A6'); a.mvkh(0, 'A6'); a.mvk32(WR, 'B6')
    a.callp(WRITE)
    a.stw('A4', 'B15', 16); a.ldw('B15', 16, 'A1'); a.nop(4)
    a.b('fl_close', 'A1'); a.nop(5)
    a.label('fl_loop')
    a.ldw('B15', 12, 'A1'); a.nop(4)                    # remaining
    a.b('fl_close', '!A1'); a.nop(5)
    a.l('add_cst', 0, 'A1', 'A6')                       # n = remaining
    a.mvk32(CHUNK, 'A9')
    a.l('cmpltu', 'A9', 'A1', 'A2')                     # CHUNK < remaining ?
    a.mvk_p(CHUNK, 'A6', 'A2')                          # if so, n = CHUNK
    a.stw('A6', 'B15', 20)                              # [5] n
    a.mvk32(FD, 'A3'); a.ldw('A3', 0, 'A4'); a.ldw('B15', 8, 'B4'); a.nop(4)
    a.ldw('B15', 20, 'A6'); a.nop(4)
    a.mvk32(WR, 'B6')
    a.callp(WRITE)
    a.stw('A4', 'B15', 16); a.ldw('B15', 16, 'A1'); a.nop(4)
    a.b('fl_close', 'A1'); a.nop(5)
    a.ldw('B15', 8, 'A5'); a.ldw('B15', 20, 'A6'); a.ldw('B15', 12, 'A7'); a.nop(4)
    a.l('add', 'A5', 'A6', 'A5'); a.stw('A5', 'B15', 8)  # ptr += n
    a.l('sub', 'A7', 'A6', 'A7'); a.stw('A7', 'B15', 12) # remaining -= n
    a.b('fl_loop'); a.nop(5)
    a.label('fl_close')
    a.mvk32(FD, 'A3'); a.ldw('A3', 0, 'A4'); a.nop(4)
    a.callp(CLOSE)
    a.mvk32(IDX, 'A3'); a.mvk32(LOG_START, 'A5'); a.stw('A5', 'A3', 0)   # clear log
    a.label('fl_done')
    a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(32, 'B15')
    a.b(ORIG_VERSION); a.nop(5)
    code = a.assemble()
    assert CAVE + len(code) <= DATA, f'code too long: {len(code)} bytes'
    data = bytearray(0x60)
    struct.pack_into('<II', data, 0, LOG_START, 0)                     # IDX, STARTED
    struct.pack_into('<4sIII', data, HDR - DATA, b'R8UL', 2, LOG_START, 0)
    p = b'A:\\UARTLOG.BIN\x00'
    data[PATH - DATA:PATH - DATA + len(p)] = p
    blob = code + b'\0' * (DATA - CAVE - len(code)) + bytes(data)
    assert CAVE + len(blob) <= CAVE_END
    return blob, a


def callp_word(site, target):
    disp = (target - (site & ~31)) // 4
    assert -(1 << 20) <= disp < (1 << 20)
    return (1 << 28) | ((disp & 0x1fffff) << 7) | (0b00100 << 2) | (1 << 1)


if __name__ == '__main__':
    blob, a = build()
    print(f'blob {len(blob)} bytes; labels', {k: hex(v) for k, v in a.labels.items() if not k.endswith('_copy')})
