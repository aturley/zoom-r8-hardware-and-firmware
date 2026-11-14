#!/usr/bin/env python3
"""Build the flash-dump routine for Zoom R8 firmware v1.17.

Hook: the VERSION menu item's action pointer (0xC06E24EC, normally 0xC07ED7CC) is redirected to this routine.
Routine: open A:\\FLASH.BIN for write, copy flash 0x000000..0x7FFFFF in 32 KB chunks through
0xC0040000 (the updater's verify buffer, idle outside update mode), close, then tail-jump to the
original VERSION handler with B3 intact. Any failure skips straight to the original handler.

Placement: 0xC06D9800 (inside the XDC Text module/instance-name table, used only when printing
diagnostic names on errors).
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import asm6x

OPEN, FREAD, WRITE, CLOSE = 0xc0763c6c, 0xc07d1550, 0xc0762db8, 0xc0765c6c
ORIG = 0xc07ed7cc
BUF, CH, FLASH_SIZE = 0xc0040000, 0x8000, 0x800000
CAVE = 0xc06d9800
CAVE_END = 0xc06d9c00
HOOK_SLOT = 0xc06e24ec
PATH = b'A:\\FLASH.BIN\x00'

# stack frame (32 bytes): [1]=B3, [2]=scratch, [3]=flash addr, [4]=chunks left


def build():
    a = asm6x.Asm(CAVE)
    data = CAVE + 0x200            # data slots after the code
    FD, WR, PTH = data, data + 4, data + 16

    a.addk(-32, 'B15')
    a.stw('B3', 'B15', 4)
    a.mvkl(0, 'A3');  a.mvkh(0, 'A3');  a.stw('A3', 'B15', 12)             # addr = 0
    a.mvkl(FLASH_SIZE // CH, 'A3'); a.mvkh(0, 'A3'); a.stw('A3', 'B15', 16)  # chunks = 256
    # rc = open(&fd, path, 0x101 (write|create), 0x80)
    a.mvk32(FD, 'A4'); a.mvk32(PTH, 'B4'); a.mvkl(0x101, 'A6'); a.mvkh(0, 'A6')
    a.mvkl(0x80, 'B6'); a.mvkh(0, 'B6')
    a.callp(OPEN)
    a.stw('A4', 'B15', 8); a.ldw('B15', 8, 'A1'); a.nop(4)
    a.b('done', 'A1'); a.nop(5)                                              # open failed -> skip
    a.label('loop')
    # rc = flash_read(addr, BUF, CH)
    a.ldw('B15', 12, 'A4'); a.nop(4)
    a.mvk32(BUF, 'B4'); a.mvkl(CH, 'A6'); a.mvkh(0, 'A6')
    a.callp(FREAD)
    a.stw('A4', 'B15', 8); a.ldw('B15', 8, 'A1'); a.nop(4)
    a.b('close', 'A1'); a.nop(5)
    # rc = write(fd, BUF, CH, &written)
    a.mvk32(FD, 'A3'); a.ldw('A3', 0, 'A4'); a.nop(4)
    a.mvk32(BUF, 'B4'); a.mvkl(CH, 'A6'); a.mvkh(0, 'A6'); a.mvk32(WR, 'B6')
    a.callp(WRITE)
    a.stw('A4', 'B15', 8); a.ldw('B15', 8, 'A1'); a.nop(4)
    a.b('close', 'A1'); a.nop(5)
    # addr += CH (two ADDKs of 0x4000; ADDK takes a signed 16-bit constant)
    a.ldw('B15', 12, 'A3'); a.nop(4)
    a.addk(0x4000, 'A3'); a.addk(0x4000, 'A3'); a.stw('A3', 'B15', 12)
    # if (--chunks) goto loop
    a.ldw('B15', 16, 'A1'); a.nop(4)
    a.addk(-1, 'A1'); a.stw('A1', 'B15', 16)
    a.b('loop', 'A1'); a.nop(5)
    a.label('close')
    a.mvk32(FD, 'A3'); a.ldw('A3', 0, 'A4'); a.nop(4)
    a.callp(CLOSE)
    a.label('done')
    a.ldw('B15', 4, 'B3'); a.nop(4)
    a.addk(32, 'B15')
    a.b(ORIG); a.nop(5)                                                      # tail-call original
    code = a.assemble()
    assert CAVE + len(code) <= data, 'code overlaps data slots'
    blob = bytearray(code + b'\0' * (data - CAVE - len(code)))
    blob += b'\0' * 16                       # FD, WR slots + pad
    blob += PATH
    while len(blob) % 32: blob += b'\0'
    assert CAVE + len(blob) <= CAVE_END
    return bytes(blob), a


if __name__ == '__main__':
    blob, a = build()
    print(f'routine {len(blob)} bytes at {CAVE:#x}; labels', {k: hex(v) for k, v in a.labels.items()})
