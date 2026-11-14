#!/usr/bin/env python3
"""Pack an L2-linked program (entry r8_boot, as for standalone_c.py) into APP.BIN for the RUN APP launcher.
Header (cbuild/launcher/app.h): 'R8AP', version 1, load, len, entry, crc32(image), top (end of uninitialised data), 0.
Usage: python3 mkapp.py PROGRAM.out APP.BIN"""
import os, sys, struct, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import celf
MAGIC, VERSION, LOAD_LO, TOP_MAX, STAGE_MAX = 0x50413852, 1, 0x11800000, 0x1182C000, 0x30000

def pack(prog):
    base, img, syms, top = celf.image(prog)
    entry = syms.get('r8_boot', base)
    assert base >= LOAD_LO and top <= TOP_MAX, f'program spans {base:#x}..{top:#x}: must fit {LOAD_LO:#x}..{TOP_MAX:#x}'
    assert 0 < len(img) <= STAGE_MAX and base <= entry < base + len(img)
    hdr = struct.pack('<8I', MAGIC, VERSION, base, len(img), entry, zlib.crc32(img), top, 0)
    return hdr + img, base, entry, top

if __name__ == '__main__':
    out, base, entry, top = pack(sys.argv[1])
    open(sys.argv[2], 'wb').write(out)
    print(f'{sys.argv[2]}: {len(out) - 32} bytes at {base:#x}, entry {entry:#x}, data up to {top:#x}, crc {zlib.crc32(out[32:]):08x}')
