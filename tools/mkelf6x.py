"""Wrap a raw C6000 code blob in a minimal ELF32 for TI dis6x: one ALLOC|EXEC .text at addr, plus the
.c6xabi.attributes section copied from a cl6x-built ELF (it tells dis6x the ISA, here C674x).
mkelf6x.py blob.bin 0xADDR out.obj ref.out"""
import sys, struct
data = open(sys.argv[1], 'rb').read(); addr = int(sys.argv[2], 16); out = sys.argv[3]; ref = open(sys.argv[4], 'rb').read()
shoff, = struct.unpack_from('<I', ref, 32); shentsize, shnum, shstrndx = struct.unpack_from('<HHH', ref, 46)
def sh(i): return struct.unpack_from('<IIIIIIIIII', ref, shoff + i * shentsize)
so = sh(shstrndx)[4]
attr = None
for i in range(shnum):
    s = sh(i); nm = ref[so + s[0]:ref.index(b'\0', so + s[0])]
    if nm == b'.c6xabi.attributes': attr = (s[1], ref[s[4]:s[4] + s[5]])
assert attr, 'no attributes section in ref'
shstr = b'\0.text\0.shstrtab\0.c6xabi.attributes\0'
ehsz, shsz = 52, 40
o_text = ehsz; o_attr = o_text + len(data); o_str = o_attr + len(attr[1]); o_sh = (o_str + len(shstr) + 3) & ~3
eflags, = struct.unpack_from('<I', ref, 36)
eh = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 1, 140, 1, 0, 0, o_sh, eflags, ehsz, 0, 0, shsz, 4, 2)
shs = bytes(shsz)
shs += struct.pack('<IIIIIIIIII', 1, 1, 6, addr, o_text, len(data), 0, 0, 32, 0)
shs += struct.pack('<IIIIIIIIII', 7, 3, 0, 0, o_str, len(shstr), 0, 0, 1, 0)
shs += struct.pack('<IIIIIIIIII', 17, attr[0], 0, 0, o_attr, len(attr[1]), 0, 0, 1, 0)
f = eh + data + attr[1] + shstr; f += bytes(o_sh - len(f)) + shs
open(out, 'wb').write(f)
