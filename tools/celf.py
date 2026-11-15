"""Load a TI cl6x ELF (.out) into raw images. image(path) -> (base, bytes, symbols, bss_end) of the lowest region (the
flash loader's CODEBUF image, an app's L2 image); regions(path) -> every region (an app's L2 and DDR parts).
Uninitialised sections (NOBITS) are not stored: the program zeroes them."""
import struct

def _sections(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'\x7fELF' and d[4] == 1 and d[5] == 1, 'not a little-endian ELF32'
    shoff, = struct.unpack_from('<I', d, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2e)
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    def name(strtab, off):
        o = sh[strtab][4] + off; return d[o:d.index(b'\0', o)].decode()
    alloc = []                                              # (addr, size, data or None for NOBITS, name)
    for s in sh:
        nm, typ, flags, addr, off, size = s[0], s[1], s[2], s[3], s[4], s[5]
        if not (flags & 2) or size == 0: continue           # SHF_ALLOC
        alloc.append((addr, size, None if typ == 8 else d[off:off + size], name(shstrndx, nm)))
    syms = {}
    for s in sh:
        if s[1] != 2: continue                              # SHT_SYMTAB
        for i in range(s[5] // 16):
            st_name, val, size, info, other, shndx = struct.unpack_from('<IIIBBH', d, s[4] + 16 * i)
            if st_name: syms[name(s[6], st_name)] = val
    return alloc, syms


def regions(path, gap=0x100000):
    """Group the allocated sections into regions (sections closer than `gap` share one).
    -> ([(base, image bytes, top)], symbols). image covers the loaded sections; top = end of all sections, NOBITS too.
    An app linked for L2 only has one region; one with DDR sections (cbuild/forth/l2.cmd) has two."""
    alloc, syms = _sections(path)
    alloc.sort()
    groups = []
    for a in alloc:
        if groups and a[0] - max(x[0] + x[1] for x in groups[-1]) < gap: groups[-1].append(a)
        else: groups.append([a])
    out = []
    for g in groups:
        top = max(a + n for a, n, _, _ in g)
        loaded = [(a, b) for a, n, b, _ in g if b is not None]
        if not loaded:
            out.append((g[0][0], b'', top)); continue
        base = min(a for a, _ in loaded); end = max(a + len(b) for a, b in loaded)
        img = bytearray(end - base)
        for a, b in loaded: img[a - base:a - base + len(b)] = b
        out.append((base, bytes(img), top))
    return out, syms


def image(path):
    """The lowest-addressed region (for an app, the L2 image): (base, bytes, symbols, end of its sections)."""
    regs, syms = regions(path)
    base, img, top = next(r for r in regs if r[1])
    return base, img, syms, top


if __name__ == '__main__':
    import sys
    base, img, syms, top = image(sys.argv[1])
    assert base == 0xc0070000 and syms.get('r8_entry') == base, 'r8_entry must be first, at 0xC0070000'
    assert len(img) <= 55640 - 0x40, 'image too big for the flash padding'
    open(sys.argv[2], 'wb').write(img)
    print(f'{sys.argv[2]}: {len(img)} bytes at {base:#x}, uninitialised data up to {top:#x}')
