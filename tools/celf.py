"""Load a TI cl6x ELF (.out) linked at CODEBUF into one raw image for the flash loader.
image(path) -> (base, bytes, symbols, bss_end). Uninitialised sections (NOBITS) are not stored: r8_entry zeroes them."""
import struct

def image(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'\x7fELF' and d[4] == 1 and d[5] == 1, 'not a little-endian ELF32'
    shoff, = struct.unpack_from('<I', d, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2e)
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    def name(strtab, off):
        o = sh[strtab][4] + off; return d[o:d.index(b'\0', o)].decode()
    load, top = [], 0
    for s in sh:
        nm, typ, flags, addr, off, size = s[0], s[1], s[2], s[3], s[4], s[5]
        if not (flags & 2) or size == 0: continue          # SHF_ALLOC
        top = max(top, addr + size)
        if typ != 8: load.append((addr, d[off:off + size], name(shstrndx, nm)))
    base = min(a for a, _, _ in load); end = max(a + len(b) for a, b, _ in load)
    img = bytearray(end - base)
    for a, b, _ in load: img[a - base:a - base + len(b)] = b
    syms = {}
    for s in sh:
        if s[1] != 2: continue                              # SHT_SYMTAB
        for i in range(s[5] // 16):
            st_name, val, size, info, other, shndx = struct.unpack_from('<IIIBBH', d, s[4] + 16 * i)
            if st_name: syms[name(s[6], st_name)] = val
    return base, bytes(img), syms, top

if __name__ == '__main__':
    import sys
    base, img, syms, top = image(sys.argv[1])
    assert base == 0xc0070000 and syms.get('r8_entry') == base, 'r8_entry must be first, at 0xC0070000'
    assert len(img) <= 55640 - 0x40, 'image too big for the flash padding'
    open(sys.argv[2], 'wb').write(img)
    print(f'{sys.argv[2]}: {len(img)} bytes at {base:#x}, uninitialised data up to {top:#x}')
