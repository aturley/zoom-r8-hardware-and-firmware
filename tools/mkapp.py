#!/usr/bin/env python3
"""Pack a program linked for the R8 (entry r8_boot) into an app file (APP.BIN, R8APPS/*.BIN).

Header, 32 bytes, eight little-endian words (cbuild/launcher/app.h, cbuild/forth/reload.c):
  'R8AP', version, load, len, entry, crc32, top, nseg
Version 1 (an app entirely in L2; what the flash launcher accepts, so the chooser must stay version 1):
  the L2 image (len bytes) follows; crc32 covers it; top = end of its uninitialised data; nseg = 0.
Version 2 (an app with sections in the DDR window, cbuild/forth/l2.cmd), loaded by reload.c (the chooser, RELOAD):
  the L2 image follows, then nseg DDR segments, each: addr, length, data (length bytes, a multiple of 4);
  crc32 covers everything after the header. DDR segments must lie in 0xC0000000-0xC00FFFFF, uninitialised data
  included (the app zeroes its .ddrbss itself), and the whole file must fit the loader's 512 KB stage.
A program with no DDR sections always gets version 1, byte for byte as before version 2 existed.
Usage: python3 mkapp.py PROGRAM.out APP.BIN"""
import os, sys, struct, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import celf
MAGIC, LOAD_LO, TOP_MAX = 0x50413852, 0x11800000, 0x1182C000
DDR_LO, DDR_HI = 0xC0000000, 0xC0100000                 # the app DDR window (cbuild/forth/l2.cmd, reload.c)
FILE_MAX = 0x80000                                       # reload.c's stage: 0xC0300000 up to fat.c's 0xC0380000


def pack_info(prog):
    """-> (file bytes, L2 base, entry, L2 top, DDR segments [(addr, data)], DDR top or None)"""
    regs, syms = celf.regions(prog)
    l2 = [r for r in regs if LOAD_LO <= r[0] < TOP_MAX]
    ddr = [r for r in regs if DDR_LO <= r[0] < DDR_HI or DDR_LO <= r[2] - 1 < DDR_HI]
    other = [r for r in regs if r not in l2 and r not in ddr]
    assert len(l2) == 1 and not other, f'sections outside L2 and the DDR window: {[(hex(b), hex(t)) for b, _, t in other]}'
    base, img, top = l2[0]
    entry = syms.get('r8_boot', base)
    assert top <= TOP_MAX, f'L2 part spans {base:#x}..{top:#x}: must end by {TOP_MAX:#x} (move code or data to DDR)'
    assert 0 < len(img) and base <= entry < base + len(img)
    segs = []
    for b, im, t in ddr:
        assert DDR_LO <= b and t <= DDR_HI, f'DDR part spans {b:#x}..{t:#x}: must fit {DDR_LO:#x}..{DDR_HI:#x}'
        if im:
            assert b % 4 == 0
            segs.append((b, im + bytes(-len(im) % 4)))
    if not segs:
        body = img
        hdr = struct.pack('<8I', MAGIC, 1, base, len(img), entry, zlib.crc32(body), top, 0)
    else:
        img = img + bytes(-len(img) % 4)
        body = img + b''.join(struct.pack('<2I', a, len(d)) + d for a, d in segs)
        hdr = struct.pack('<8I', MAGIC, 2, base, len(img), entry, zlib.crc32(body), top, len(segs))
    assert 32 + len(body) <= FILE_MAX, f'app file is {32 + len(body)} bytes: the loader stages at most {FILE_MAX}'
    ddr_top = max((t for _, _, t in ddr), default=None)
    return hdr + body, base, entry, top, segs, ddr_top


def pack(prog):
    """-> (file bytes, L2 base, entry, L2 top), as before version 2 existed"""
    return pack_info(prog)[:4]


if __name__ == '__main__':
    out, base, entry, top, segs, ddr_top = pack_info(sys.argv[1])
    open(sys.argv[2], 'wb').write(out)
    msg = f'{sys.argv[2]}: {len(out) - 32} bytes at {base:#x}, entry {entry:#x}, data up to {top:#x}, crc {zlib.crc32(out[32:]):08x}'
    if ddr_top:
        msg += f'; DDR {", ".join(f"{a:#x}+{len(d)}" for a, d in segs) or "uninitialised only"}, up to {ddr_top:#x} (version 2)'
    print(msg)
