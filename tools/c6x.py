"""Minimal TI C64x+/C674x decoder for reverse-engineering (not a full disassembler).

Handles: 32-byte fetch packets, compact-instruction headers (layout/p-bits),
32-bit MVK/MVKL/MVKH, B/CALLP displacement branches, B reg, ADDK, ADDKPC,
D-unit loads/stores (base+offset and B14/B15 + ucst15), NOP. Everything else
is printed as raw with its functional unit guessed.
"""
import struct
import c16

LDST = {0: 'LDHU', 1: 'LDBU', 2: 'LDB', 3: 'STB', 4: 'LDH', 5: 'STH', 6: 'LDW', 7: 'STW'}
LDST_SZ = {0: 2, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 4, 7: 4}
CREG = {0: '', 1: 'B0', 2: 'B1', 3: 'B2', 4: 'A1', 5: 'A2', 6: 'A0'}
MODE = {0: '*-{b}[{o}]', 1: '*+{b}[{o}]', 8: '*--{b}[{o}]', 9: '*++{b}[{o}]',
        10: '*{b}--[{o}]', 11: '*{b}++[{o}]',
        4: '*-{b}[{r}]', 5: '*+{b}[{r}]', 12: '*--{b}[{r}]', 13: '*++{b}[{r}]',
        14: '*{b}--[{r}]', 15: '*{b}++[{r}]'}


def sx(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def reg(side, n):
    return ('A' if side == 0 else 'B') + str(n)


class Insn:
    __slots__ = ('addr', 'size', 'raw', 'text', 'kind', 'target', 'dst', 'imm', 'p')

    def __init__(self, addr, size, raw, text, kind='', target=None, dst=None, imm=None, p=0):
        self.addr, self.size, self.raw, self.text, self.kind = addr, size, raw, text, kind
        self.target, self.dst, self.imm, self.p = target, dst, imm, p


def pred(w):
    c, z = (w >> 29) & 7, (w >> 28) & 1
    r = CREG.get(c, '')
    if not r:
        return ''
    return f'[{"!" if z else ""}{r}] '


def dec32(w, addr, pce1, hdrpk=False):
    p = w & 1
    s = (w >> 1) & 1
    pr = pred(w)
    op5 = (w >> 2) & 0x1f
    dst = (w >> 23) & 0x1f
    # MVK / MVKL (h=0) and MVKH (h=1) : bits 5-2 = 1010
    if (w >> 2) & 0xf == 0b1010:
        cst = (w >> 7) & 0xffff
        if (w >> 6) & 1:
            return Insn(addr, 4, w, f'{pr}MVKH  .S{s+1} 0x{cst:04x}0000,{reg(s,dst)}', 'mvkh', dst=(s, dst), imm=cst, p=p)
        return Insn(addr, 4, w, f'{pr}MVKL  .S{s+1} 0x{cst:04x},{reg(s,dst)}', 'mvkl', dst=(s, dst), imm=cst, p=p)
    # B disp / CALLP : bits 6-2 = 00100
    if op5 == 0b00100:
        cst = sx((w >> 7) & 0x1fffff, 21)
        t = (pce1 + (cst << 2)) & 0xffffffff
        if (w >> 28) == 1:  # creg=000 z=1 -> CALLP
            return Insn(addr, 4, w, f'CALLP .S{s+1} 0x{t:08x},{reg(s,3)}', 'call', target=t, p=p)
        return Insn(addr, 4, w, f'{pr}B     .S{s+1} 0x{t:08x}', 'b', target=t, p=p)
    # BNOP disp (C64x+): bits 12-2 = 00001001000, cst12 in 27-16, N3 in 15-13
    if (w & 0x1ffc) == 0x0120:
        cst = sx((w >> 16) & 0xfff, 12)
        t = (pce1 + (cst << (1 if hdrpk else 2))) & 0xffffffff   # halfwords in header packets (validated 100%)
        return Insn(addr, 4, w, f'{pr}BNOP  .S{s+1} 0x{t:08x},{(w >> 13) & 7}', 'b', target=t, p=p)
    # ADDK : bits 6-2 = 10100
    if op5 == 0b10100:
        cst = sx((w >> 7) & 0xffff, 16)
        return Insn(addr, 4, w, f'{pr}ADDK  .S{s+1} {cst},{reg(s,dst)}', 'addk', p=p)
    # D unit load/store base+offset: bits 3-2 = 01
    if (w >> 2) & 3 == 0b01:
        op = (w >> 4) & 7
        y = (w >> 7) & 1
        r = (w >> 8) & 1
        mode = (w >> 9) & 0xf
        off = (w >> 13) & 0x1f
        base = (w >> 18) & 0x1f
        name = LDST[op] if not r else {6: 'LDNDW?', 7: 'STNDW?', 2: 'LDDW', 3: 'STDW', 4: 'LDNW', 5: 'STNW'}.get(op, f'LDST{op}r')
        fmt = MODE.get(mode, '*?{b}[{o}]')
        sz = LDST_SZ[op] if not r else (8 if op in (2, 3, 6, 7) else 4)
        ea = fmt.format(b=reg(y, base), o=off, r=reg(y, off))
        return Insn(addr, 4, w, f'{pr}{name:5s} .D{y+1}T{s+1} {ea},{reg(s,dst)}' if name.startswith('LD')
                    else f'{pr}{name:5s} .D{y+1}T{s+1} {reg(s,dst)},{ea}',
                    'ld' if name.startswith('LD') else 'st', imm=(y, base, mode, off * sz), dst=(s, dst), p=p)
    # D unit load/store B14/B15 + ucst15 : bits 3-2 = 11
    if (w >> 2) & 3 == 0b11:
        op = (w >> 4) & 7
        y = (w >> 7) & 1
        u = (w >> 8) & 0x7fff
        b = 'B15' if y else 'B14'
        off = u * LDST_SZ[op]
        name = LDST[op]
        return Insn(addr, 4, w, (f'{pr}{name:5s} .D2T{s+1} *+{b}[{u}],{reg(s,dst)}' if name.startswith('LD')
                                 else f'{pr}{name:5s} .D2T{s+1} {reg(s,dst)},*+{b}[{u}]') + f'   ; {b}+0x{off:x}',
                    'gld' if name.startswith('LD') else 'gst', imm=(y, off), dst=(s, dst), p=p)
    # NOP
    if w & 0xfffe1ffe == 0 and w:
        return Insn(addr, 4, w, f'NOP   {((w >> 13) & 0xf) + 1}', 'nop', p=p)
    if w & ~1 == 0:
        return Insn(addr, 4, w, 'NOP', 'nop', p=p)
    # B src2 (S2) : bits 11-2 = 0011011000
    if (w >> 2) & 0x3ff == 0b0011011000:
        src2 = (w >> 18) & 0x1f
        x = (w >> 12) & 1
        txt = f'{pr}B     .S2 {reg(1 ^ x if False else (1 if not x else 0), src2)}'
        k = 'ret' if src2 == 3 and not x else 'bind'
        return Insn(addr, 4, w, txt + ('   ; return' if k == 'ret' else ''), k, p=p)
    # BNOP src2 (C64x+): bits 11-2 = 0011011000 with N3 in 15-13 handled above; ADDKPC: bits 12-2 = 00001011000
    if (w >> 2) & 0x7ff == 0b00001011000:
        cst = sx((w >> 16) & 0x7f, 7)
        return Insn(addr, 4, w, f'{pr}ADDKPC .S2 0x{(pce1 + (cst << 2)) & 0xffffffff:08x},{reg(1,dst)},{((w >> 13) & 7)}', 'addkpc', p=p)
    # unit guess
    if (w >> 2) & 7 == 0b110:
        u = 'L'
    elif op5 == 0:
        u = 'M'
    elif (w >> 2) & 0xf == 0b1000:
        u = 'S'
    elif op5 == 0b10000:
        u = 'D'
    else:
        u = '?'
    return Insn(addr, 4, w, f'{pr}.{u}{s+1}   <{w:08x}>', 'other', p=p)


def decode_packet(data, off, addr):
    """Decode one 32-byte fetch packet at data[off:], virtual address addr."""
    ws = struct.unpack_from('<8I', data, off)
    out = []
    hdr = ws[7]
    if hdr >> 28 == 0xE:
        layout = (hdr >> 21) & 0x7f
        pbits = hdr & 0x3fff
        H = c16.Hdr(hdr)
        for i in range(7):
            a = addr + 4 * i
            if layout & (1 << i):
                for h in range(2):
                    hw = (ws[i] >> (16 * h)) & 0xffff
                    pb = (pbits >> (2 * i + h)) & 1
                    txt, kind, ex = c16.dec16(hw, a + 2 * h, addr, H)
                    out.append(Insn(a + 2 * h, 2, hw, txt + '  (c16)' if kind != 'c16' else txt, kind,
                                    target=ex.get('target'), dst=ex.get('dst'), imm=ex.get('imm'), p=pb))
            else:
                out.append(dec32(ws[i], a, addr, True))
        out.append(Insn(addr + 28, 4, hdr, f'.fphead layout={layout:07b}', 'hdr'))
    else:
        for i in range(8):
            out.append(dec32(ws[i], addr + 4 * i, addr))
    return out


def decode_range(data, base, start=None, end=None):
    start = base if start is None else start
    end = base + len(data) if end is None else end
    a = start & ~31
    res = []
    while a < end:
        o = a - base
        if o >= 0 and o + 32 <= len(data):
            res.extend(decode_packet(data, o, a))
        a += 32
    return res
