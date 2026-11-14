#!/usr/bin/env python3
"""Pack a DDR-linked utility (entry r8_entry, cbuild/utils/*.cmd) into an R8UT file for RUN UTIL.
Header (cbuild/launcher/util.h): 'R8UT', version 1, load, len, entry, crc32(image), top (end of uninitialised data), 0.
Usage: python3 mkutil.py PROGRAM.out OUT.BIN"""
import os, sys, struct, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import celf
MAGIC, VERSION, LO, HI = 0x54553852, 1, 0xC0100000, 0xC0240000

def pack(prog):
    base, img, syms, top = celf.image(prog)
    entry = syms.get('r8_entry', base)
    assert base >= LO and top <= HI, f'utility spans {base:#x}..{top:#x}: must fit {LO:#x}..{HI:#x}'
    assert 0 < len(img) and base <= entry < base + len(img)
    hdr = struct.pack('<8I', MAGIC, VERSION, base, len(img), entry, zlib.crc32(img), top, 0)
    return hdr + img, base, entry, top

if __name__ == '__main__':
    out, base, entry, top = pack(sys.argv[1])
    open(sys.argv[2], 'wb').write(out)
    print(f'{sys.argv[2]}: {len(out) - 32} bytes at {base:#x}, entry {entry:#x}, data up to {top:#x}, crc {zlib.crc32(out[32:]):08x}')
