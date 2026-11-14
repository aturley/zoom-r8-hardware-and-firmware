"""C64x+ compact (16-bit) instruction decoder.

Format masks/values and field layouts follow GNU binutils include/opcode/tic6x-insn-formats.h
(gdb 8/9 era). Mnemonics for the .L/.S/.D/LSD forms come from the binutils opcode table and the
gas testsuite (insns16-lsd-unit.d). Where the public sources we could read did not give the
op-field meaning, the mnemonic is printed with a trailing '?'.

Header expansion bits (fetch-packet header): SAT=bit14, BR=bit15, DSZ=bits16-18, RS=bit19, PROT=bit20.
Compact register fields are 3 bits: A0-A7/B0-B7, or A16-A23/B16-B23 when RS=1.
"""


def bits(v, lo, w):
    return (v >> lo) & ((1 << w) - 1)


def sx(v, n):
    return v - (1 << n) if v & (1 << (n - 1)) else v


class Hdr:
    def __init__(self, w):
        self.sat = (w >> 14) & 1
        self.br = (w >> 15) & 1
        self.dsz = (w >> 16) & 7
        self.rs = (w >> 19) & 1
        self.prot = (w >> 20) & 1


def R(side, n, rs=0):
    return ('A' if side == 0 else 'B') + str(n + (16 if rs else 0))


# (name, mask, value, header-condition)
FORMATS = [
    # .S branches (need BR=1)
    ('s_sbu8c', 0xc02e, 0xc02a, 'br1'),
    ('s_sbu8', 0xc03e, 0xc00a, 'br1'),
    ('s_sbs7c', 0x002e, 0x002a, 'br1'),
    ('s_sbs7', 0x003e, 0x000a, 'br1'),
    ('s_scs10', 0x003e, 0x001a, 'br1'),
    # no-unit
    ('nfu_unop', 0x1fff, 0x0c6e, None),
    ('nfu_uspldr', 0xbc7e, 0x8c66, None),
    ('nfu_uspl', 0xbc7e, 0x0c66, None),
    ('nfu_uspk', 0x3c7e, 0x1c66, None),
    ('nfu_uspma', 0x3c7e, 0x2c66, None),
    ('nfu_uspmb', 0x3c7e, 0x3c66, None),
    # LSD
    ('lsdx1c', 0x1c66, 0x0866, None),
    ('lsdx1', 0x1c66, 0x1866, None),
    ('lsdmvto', 0x0066, 0x0006, None),
    ('lsdmvfr', 0x0066, 0x0046, None),
    # .S misc
    ('s_sx1b', 0x187e, 0x006e, None),
    ('s_s2sh', 0x047e, 0x0462, None),
    ('s_s2ext', 0x047e, 0x0062, None),
    ('s_sx2op', 0x047e, 0x002e, None),
    ('s_sx5', 0x047e, 0x042e, None),
    ('s_ssh5', 0x041e, 0x0402, None),
    ('s_sc5', 0x041e, 0x0002, None),
    ('s_smvk8', 0x001e, 0x0012, None),
    ('s_s3i', 0x040e, 0x040a, 'br0'),
    ('s_s3', 0x040e, 0x000a, 'br0'),
    # .L
    ('l_lx1c', 0x147e, 0x1026, None),
    ('l_lx3c', 0x147e, 0x0026, None),
    ('l_lx5', 0x047e, 0x0426, None),
    ('l_l2c', 0x040e, 0x0408, None),
    ('l_l3i', 0x040e, 0x0400, None),
    ('l_l3', 0x040e, 0x0000, None),
    # .M
    ('m_m3', 0x001e, 0x001e, None),
    # .D
    ('d_dx5p', 0x1c7e, 0x0c76, None),
    ('d_dpp', 0x087f, 0x0077, None),
    ('d_dx2op', 0x047e, 0x0036, None),
    ('d_dx5', 0x047e, 0x0436, None),
    ('d_dstk', 0x8c06, 0x8c04, None),
    ('d_dinc', 0xcc06, 0x0c04, None),
    ('d_ddec', 0xcc06, 0x4c04, None),
    ('d_dind', 0x0c06, 0x0404, None),
    ('d_doff4', 0x0406, 0x0004, None),
]
FORMATS.sort(key=lambda f: -bin(f[1]).count('1'))  # most specific first

L2C = {0: 'AND', 1: 'OR?', 2: 'XOR?', 3: 'CMPEQ', 4: 'CMPLT', 5: 'CMPGT', 6: 'CMPLTU', 7: 'CMPGTU'}
LX1C = {0: 'CMPLT', 1: 'CMPGT', 2: 'CMPLTU', 3: 'CMPGTU'}
LSDX1 = {0: 'MVK 0', 1: 'MVK 1', 3: 'SUB 1', 5: 'ADD 1', 7: 'XOR 1'}  # op3 = binutils "add -1" (.L/.S)
CC = {0: '[A0]', 1: '[!A0]', 2: '[B0]', 3: '[!B0]'}


def ldst_size(op_load, sz, dsz):
    """Best reading of SPRU732 Table 3-10: sz=0 -> word (or doubleword when DSZ=1xx);
    sz=1 -> DSZ low bits pick BU/B/HU/H (loads) or B/H (stores)."""
    if sz == 0:
        return ('LDW', 'STW') if dsz < 4 else ('LDDW?', 'STDW?')
    k = dsz & 3
    return [('LDBU', 'STB'), ('LDB', 'STB'), ('LDHU', 'STH'), ('LDH', 'STH')][k] if op_load is not None else None


def dec16(hw, addr, pce1, h):
    """Return (text, kind, extra) for a compact instruction. kind in:
    call, b, bind, ret, mvk, mv, ld, st, nop, alu, other."""
    s = hw & 1
    unit_side = s + 1
    for name, mask, val, cond in FORMATS:
        if hw & mask != val:
            continue
        if cond == 'br1' and not h.br:
            continue
        if cond == 'br0' and h.br:
            continue
        rs = h.rs
        if name == 's_scs10':
            t = (pce1 + (sx(bits(hw, 6, 10), 10) << 2)) & 0xffffffff
            return f'CALLP .S{unit_side} 0x{t:08x}', 'call', {'target': t}
        if name in ('s_sbs7', 's_sbs7c'):
            t = (pce1 + (sx(bits(hw, 6, 7), 7) << 1)) & 0xffffffff  # halfword units (validated: 100% execute-packet starts)
            n = bits(hw, 13, 3)
            pre = ''
            if name == 's_sbs7c':
                pre = f'[{"!" if bits(hw, 4, 1) else ""}{"A0" if s == 0 else "B0"}] '
            return f'{pre}BNOP  .S{unit_side} 0x{t:08x},{n}', 'b', {'target': t, 'cond': bool(pre)}
        if name in ('s_sbu8', 's_sbu8c'):
            t = (pce1 + (bits(hw, 6, 8) << 1)) & 0xffffffff  # halfword units
            pre = ''
            if name == 's_sbu8c':
                pre = f'[{"!" if bits(hw, 4, 1) else ""}{"A0" if s == 0 else "B0"}] '
            return f'{pre}BNOP  .S{unit_side} 0x{t:08x},5', 'b', {'target': t, 'cond': bool(pre)}
        if name == 's_sx1b':
            r = bits(hw, 7, 4)
            n = bits(hw, 13, 3)
            k = 'ret' if r == 3 else 'bind'
            return f'BNOP  .S{unit_side} B{r},{n}' + ('   ; return' if k == 'ret' else ''), k, {}
        if name == 'nfu_unop':
            return f'NOP   {bits(hw, 13, 3) + 1}', 'nop', {}
        if name.startswith('nfu_usp'):
            mn = {'nfu_uspl': 'SPLOOP', 'nfu_uspldr': 'SPLOOPD/W', 'nfu_uspk': 'SPKERNEL',
                  'nfu_uspma': 'SPMASK', 'nfu_uspmb': 'SPMASKR'}[name]
            return f'{mn} <{hw:04x}>', 'sploop', {}
        if name == 'lsdx1c':
            u = 'LSD?'[bits(hw, 3, 2)]
            d = R(s, bits(hw, 7, 3), rs)
            c = bits(hw, 13, 1)
            return f'{CC[bits(hw, 14, 2)]} MVK .{u}{unit_side} {c},{d}', 'mvk', {'dst': (s, bits(hw, 7, 3) + 16 * rs), 'imm': c, 'cond': True}
        if name == 'lsdx1':
            u = 'LSD?'[bits(hw, 3, 2)]
            r = bits(hw, 7, 3)
            op = bits(hw, 13, 3)
            d = R(s, r, rs)
            if op in (0, 1):
                return f'MVK   .{u}{unit_side} {op},{d}', 'mvk', {'dst': (s, r + 16 * rs), 'imm': op}
            mn = LSDX1.get(op, f'LSDX1op{op}?')
            return f'{mn.split()[0]:5s} .{u}{unit_side} {d}{"," + mn.split()[1] if " " in mn else ""},{d}', 'alu', {}
        if name in ('lsdmvto', 'lsdmvfr'):
            u = 'LSD?'[bits(hw, 3, 2)]
            x = bits(hw, 12, 1)
            if name == 'lsdmvto':
                src = bits(hw, 7, 3) | (bits(hw, 10, 2) << 3)
                dst = bits(hw, 13, 3) + 16 * rs
            else:
                src = bits(hw, 7, 3) + 16 * rs
                dst = bits(hw, 13, 3) | (bits(hw, 10, 2) << 3)
            sside = s ^ x
            return f'MV    .{u}{unit_side}{"X" if x else ""} {R(sside, src)},{R(s, dst)}', 'mv', {'src': (sside, src), 'dst': (s, dst)}
        if name == 's_smvk8':
            c = (bits(hw, 10, 1) << 7) | (bits(hw, 5, 2) << 5) | (bits(hw, 11, 2) << 3) | bits(hw, 13, 3)
            r = bits(hw, 7, 3)
            return f'MVK   .S{unit_side} {c},{R(s, r, rs)}', 'mvk', {'dst': (s, r + 16 * rs), 'imm': c}
        if name == 'l_lx5':
            c = sx((bits(hw, 11, 2) << 3) | bits(hw, 13, 3), 5)
            r = bits(hw, 7, 3)
            return f'MVK   .L{unit_side} {c},{R(s, r, rs)}', 'mvk', {'dst': (s, r + 16 * rs), 'imm': c}
        if name == 's_sx5':
            c = (bits(hw, 11, 2) << 3) | bits(hw, 13, 3)
            return f'ADDK  .S{unit_side} {c},{R(s, bits(hw, 7, 3), rs)}', 'alu', {}
        if name == 'd_dx5':
            c = (bits(hw, 11, 2) << 3) | bits(hw, 13, 3)
            return f'ADDAW .D{unit_side} B15,{c},{R(s, bits(hw, 7, 3), rs)}', 'alu', {}
        if name == 'd_dx5p':
            c = (bits(hw, 8, 2) << 3) | bits(hw, 13, 3)
            mn = 'ADDAW' if bits(hw, 7, 1) == 0 else 'SUBAW'
            return f'{mn} .D2 B15,{c},B15', 'alu', {}
        if name in ('s_s3', 'l_l3', 'd_dx2op', 's_sx2op', 's_s3i', 'l_l3i', 'm_m3'):
            u = name[0].upper()
            x = bits(hw, 12, 1)
            if name in ('d_dx2op', 's_sx2op'):
                sd = R(s, bits(hw, 13, 3), rs)
                s2 = R(s ^ x, bits(hw, 7, 3), rs)
                mn = 'ADD' if bits(hw, 11, 1) == 0 else 'SUB'
                return f'{mn:5s} .{u}{unit_side}{"X" if x else ""} {sd},{s2},{sd}', 'alu', {}
            if name == 'm_m3':
                return f'MPY?  .M{unit_side}{"X" if x else ""} <{hw:04x}>', 'alu', {}
            d = R(s, bits(hw, 4, 3), rs)
            s2 = R(s ^ x, bits(hw, 7, 3), rs)
            if name in ('s_s3i', 'l_l3i'):
                c = bits(hw, 13, 3) if name == 's_s3i' else (bits(hw, 13, 3) | (bits(hw, 11, 1) << 3))
                if name == 'l_l3i' and c == 0:
                    c = 8
                mn = ('ADD' if bits(hw, 11, 1) == 0 else 'SUB') if name == 's_s3i' else 'ADD'
                return f'{mn:5s} .{u}{unit_side}{"X" if x else ""} {s2},{c},{d}', 'alu', {}
            s1 = R(s, bits(hw, 13, 3), rs)
            mn = 'ADD' if bits(hw, 11, 1) == 0 else 'SUB'
            if h.sat:
                mn = 'S' + mn
            return f'{mn:5s} .{u}{unit_side}{"X" if x else ""} {s1},{s2},{d}', 'alu', {}
        if name == 'l_l2c':
            x = bits(hw, 12, 1)
            op = bits(hw, 5, 2) | (bits(hw, 11, 1) << 2)
            d = R(s, bits(hw, 4, 1), 0)
            return f'{L2C[op]:6s} .L{unit_side}{"X" if x else ""} {R(s, bits(hw, 13, 3), rs)},{R(s ^ x, bits(hw, 7, 3), rs)},{d}', 'alu', {}
        if name == 'l_lx1c':
            d = R(s, bits(hw, 11, 1), 0)
            return f'{LX1C[bits(hw, 14, 2)]:6s} .L{unit_side} {bits(hw, 13, 1)},{R(s, bits(hw, 7, 3), rs)},{d}', 'alu', {}
        if name == 'l_lx3c':
            d = R(s, bits(hw, 11, 1), 0)
            return f'CMPEQ  .L{unit_side} {bits(hw, 13, 3)},{R(s, bits(hw, 7, 3), rs)},{d}', 'alu', {}
        if name in ('s_ssh5', 's_sc5', 's_s2sh', 's_s2ext'):
            r = R(s, bits(hw, 7, 3), rs)
            if name == 's_sc5':
                op = bits(hw, 5, 2)
                c = (bits(hw, 11, 2) << 3) | bits(hw, 13, 3)
                mn = {1: 'SET', 2: 'CLR'}.get(op, f'SC5op{op}?')  # SET inferred (single-bit, consistent with flash offsets)
                return f'{mn:5s} .S{unit_side} {r},{c},{r}', 'alu', {}
            if name == 's_ssh5':
                op = bits(hw, 5, 2)
                c = (bits(hw, 11, 2) << 3) | bits(hw, 13, 3)
                return f'SH{op}?  .S{unit_side} {r},{c},{r}', 'alu', {}
            return f'{name[2:].upper()}? .S{unit_side} <{hw:04x}>', 'alu', {}
        if name == 'd_dstk':
            op = bits(hw, 3, 1)
            r = R(bits(hw, 12, 1), bits(hw, 4, 3), rs)
            c = (bits(hw, 7, 3) << 2) | bits(hw, 13, 2)
            if op:
                return f'LDW   .D2T{bits(hw,12,1)+1} *+B15[{c}],{r}', 'ld', {}
            return f'STW   .D2T{bits(hw,12,1)+1} {r},*+B15[{c}]', 'st', {}
        if name == 'd_dpp':
            r = R(bits(hw, 12, 1), bits(hw, 7, 4), 0)
            c = bits(hw, 13, 1) + 1
            dw = bits(hw, 15, 1)
            op = bits(hw, 14, 1)
            if op:
                return f'LD{"DW" if dw else "W"}  .D2 *++B15[{c}],{r}   ; pop', 'ld', {}
            return f'ST{"DW" if dw else "W"}  .D2 {r},*B15--[{c}]   ; push', 'st', {}
        if name in ('d_doff4', 'd_dind', 'd_dinc', 'd_ddec'):
            op = bits(hw, 3, 1)  # 1 = load
            t = bits(hw, 12, 1)
            sz = bits(hw, 9, 1)
            if h.dsz >= 4 and sz == 0:
                r = R(t, bits(hw, 5, 2) * 2, rs) + ':' + R(t, bits(hw, 5, 2) * 2 + 1, rs).lstrip('AB')
                na = bits(hw, 4, 1)
                mn = ('LDNDW' if na else 'LDDW') if op else ('STNDW' if na else 'STDW')
            else:
                r = R(t, bits(hw, 4, 3), rs)
                mn = ldst_size(op, sz, h.dsz)[0 if op else 1]
            ptr = R(s, 4 + bits(hw, 7, 2), rs)
            if name == 'd_doff4':
                c = bits(hw, 13, 3) | (bits(hw, 11, 1) << 3)
                ea = f'*+{ptr}[{c}]'
            elif name == 'd_dind':
                ea = f'*+{ptr}[{R(s, bits(hw, 13, 3), rs)}]'
            elif name == 'd_dinc':
                ea = f'*{ptr}++[{bits(hw, 13, 1) + 1}]'
            else:
                ea = f'*--{ptr}[{bits(hw, 13, 1) + 1}]'
            if op:
                return f'{mn:5s} .D{unit_side}T{t+1} {ea},{r}', 'ld', {}
            return f'{mn:5s} .D{unit_side}T{t+1} {r},{ea}', 'st', {}
        return f'{name}? <{hw:04x}>', 'other', {}
    return f'<c16 {hw:04x}>', 'c16', {}
