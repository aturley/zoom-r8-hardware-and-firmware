"""C674x instruction-set simulator for code built with cl6x (cbuild/build.sh flags: --no_compress, --disable:sploop).

Covers the 32-bit (non-compact) instruction forms the compiler and the TI runtime helpers emit for our code:
integer .L/.S/.M/.D, loads/stores (all addressing modes, B14/B15 15-bit offsets), branches (B, BNOP, BDEC, BPOS,
CALLP, ADDKPC, B reg), MVC, DINT/RINT, and the single/double float ops. Anything else raises Unsupported.

Timing model (what correctness depends on, not cycle accuracy):
  * an execute packet reads all its operands before any of its results are written;
  * results appear after the instruction's delay slots (loads 4, branches 5, MPY 1, MPY32/float 3, ...);
  * memory is accessed in E3 (2 cycles after issue), loads write their register 4 cycles after issue;
  * NOP n / BNOP / ADDKPC / CALLP idle cycles are cut short by a branch that lands during them.
No stalls, caches, interrupts or SPLOOP.

decode(word, addr) -> Ins with .mn .unit .pred .ops (structured operands, comparable with dis6x) and .ex
Sim(...).call(entry, args) runs a function until it returns to a sentinel address."""
import struct, math

class Unsupported(Exception): pass

# ---------------------------------------------------------------- registers
def rname(i): return ('A' if i < 32 else 'B') + str(i & 31)
def ridx(side, n): return side * 32 + n
def pname(i): return f'{rname(i + 1)}:{rname(i)}'        # pair, i = even register
PRED = {1: 32 + 0, 2: 32 + 1, 3: 32 + 2, 4: 1, 5: 2, 6: 0}   # creg -> B0 B1 B2 A1 A2 A0
M32 = 0xffffffff

def sx(v, n): v &= (1 << n) - 1; return v - (1 << n) if v >> (n - 1) else v
def s32(v): v &= M32; return v - (1 << 32) if v >> 31 else v

# ---------------------------------------------------------------- float helpers (C67x: denormals flush to zero)
def f2u(f):
    try: return struct.unpack('<I', struct.pack('<f', f))[0]
    except OverflowError: return 0x7f800000 if f > 0 else 0xff800000
def u2f(u):
    u &= M32
    if (u & 0x7f800000) == 0: u &= 0x80000000            # denormal input -> 0
    return struct.unpack('<f', struct.pack('<I', u))[0]
def fres(f):
    """round a python float to single precision, flushing denormal results to zero"""
    if f != f: return 0x7fffffff
    u = f2u(f)
    if (u & 0x7f800000) == 0: u &= 0x80000000
    return u
def d2u(f): return struct.unpack('<Q', struct.pack('<d', f))[0]
def u2d(u):
    if (u & 0x7ff0000000000000) == 0: u &= 1 << 63
    return struct.unpack('<d', struct.pack('<Q', u))[0]
def dres(f):
    if f != f: return 0x7fffffffffffffff
    u = d2u(f)
    if (u & 0x7ff0000000000000) == 0: u &= 1 << 63
    return u
def sat_int(f, trunc):
    if f != f: return 0x80000000
    v = math.trunc(f) if trunc else round_even(f)
    if v > 0x7fffffff: return 0x7fffffff
    if v < -0x80000000: return 0x80000000
    return v & M32
def round_even(f):
    r = math.floor(f + 0.5)
    if r - f == 0.5 and r % 2: r -= 1
    return r

# ---------------------------------------------------------------- instruction object
class Ins:
    __slots__ = ('addr', 'w', 'p', 'mn', 'unit', 'pred', 'ops', 'ex', 'nops', 'kind')
    def __init__(self, addr, w):
        self.addr, self.w, self.p = addr, w, w & 1
        self.pred = None; self.ops = []; self.ex = None; self.nops = 0; self.kind = ''; self.unit = ''; self.mn = ''
    def text(self):
        def o(x):
            if isinstance(x, tuple):
                if x[0] == 'pc': return f'0x{x[1]:08x}'
                if x[0] == 'mem':
                    _, pre, base, post, br, off = x
                    off = off if isinstance(off, str) else str(off)
                    return f'*{pre}{base}{post}' + (f'[{off}]' if br == '[' else f'({off})')
            if isinstance(x, int): return hex(x)
            return str(x)
        pr = ''
        if self.pred: pr = f'[{"!" if self.pred[1] else " "}{rname(self.pred[0])}] '
        return f'{pr}{self.mn}' + (f'.{self.unit}' if self.unit else '') + ' ' + ','.join(o(x) for x in self.ops)

# Each .ex(sim, R) runs with R = register values at the start of the packet and calls
#   sim.w(reg, value, delay)       register write (value 32 bits) visible `delay`+1 cycles after issue
#   sim.w64(even_reg, value, delay)
#   sim.mem(fn)                    memory access in E3
#   sim.br(target), sim.nop(n)     branch after 5 delay slots / extra idle cycles after this packet

def dp_ex(srcs, fn, d, out, dl):
    """Double-precision timing. srcs: [(reg, is_pair, cycle offset at which the high word is read)].
    out 'dp': low word visible after dl-1 delay slots, high word after dl; out 'r': 32-bit result after dl."""
    offs = sorted({h for r, pair, h in srcs if pair and h})
    def ex(sim, R):
        c0 = sim.cycle
        lo = [R[r] for r, pair, h in srcs]
        hi = [R[r + 1] if pair and not h else 0 for r, pair, h in srcs]
        def finish(sim):
            res = fn(*[lo[i] | (hi[i] << 32 if srcs[i][1] else 0) for i in range(len(srcs))])
            if out == 'dp':
                sim.pend.append((c0 + dl, d, res & M32)); sim.pend.append((c0 + dl + 1, d + 1, (res >> 32) & M32))
            else:
                sim.pend.append((c0 + dl + 1, d, res & M32))
        if not offs: finish(sim); return
        for k in offs:
            def rd(sim, k=k):
                for i, (r, pair, h) in enumerate(srcs):
                    if pair and h == k: hi[i] = sim.R[r + 1]
                if k == offs[-1]: finish(sim)
            sim.pend.append((c0 + k, 'late', rd))
    return ex

def opv(R, k, v):
    """operand value: k = 'r' register index v, 'c' constant v"""
    return R[v] if k == 'r' else v & M32

# ---------------------------------------------------------------- .L unit
def _L_ops():
    t = {}
    def add(op, mn, s1, s2, d, fn, dl=0): t[op] = (mn, s1, s2, d, fn, dl)
    a, u = s32, lambda v: v & M32
    add(0b0000011, 'ADD', 'r', 'x', 'r', lambda x, y: x + y)
    add(0b0000010, 'ADD', 'c5', 'x', 'r', lambda x, y: x + y)
    add(0b0100011, 'ADD', 'r', 'x', 'l', lambda x, y: a(x) + a(y))
    add(0b0100001, 'ADD', 'x', 'lp', 'l', lambda x, y: a(x) + y, 0)       # src1 field = xsint? handled below
    add(0b0100000, 'ADD', 'c5', 'lp', 'l', lambda x, y: a(x) + y)
    add(0b0101011, 'ADDU', 'r', 'x', 'ul', lambda x, y: x + y)
    add(0b0101001, 'ADDU', 'x', 'lpu', 'ul', lambda x, y: x + y)
    add(0b0000111, 'SUB', 'r', 'x', 'r', lambda x, y: x - y)
    add(0b0010111, 'SUB', 'x', 'r', 'r', lambda x, y: x - y)             # src2 - src1 (operands printed src2,src1)
    add(0b0000110, 'SUB', 'c5', 'x', 'r', lambda x, y: x - y)
    add(0b0100111, 'SUB', 'r', 'x', 'l', lambda x, y: a(x) - a(y))
    add(0b0110111, 'SUB', 'x', 'r', 'l', lambda x, y: a(x) - a(y))
    add(0b0101111, 'SUBU', 'r', 'x', 'ul', lambda x, y: x - y)
    add(0b0111111, 'SUBU', 'x', 'r', 'ul', lambda x, y: x - y)
    add(0b1001011, 'SUBC', 'r', 'x', 'r', None)
    add(0b1111111, 'OR', 'r', 'x', 'r', lambda x, y: x | y)
    add(0b1111110, 'OR', 'c5', 'x', 'r', lambda x, y: x | y)
    add(0b1111011, 'AND', 'r', 'x', 'r', lambda x, y: x & y)
    add(0b1111010, 'AND', 'c5', 'x', 'r', lambda x, y: x & y)
    add(0b1101111, 'XOR', 'r', 'x', 'r', lambda x, y: x ^ y)
    add(0b1101110, 'XOR', 'c5', 'x', 'r', lambda x, y: x ^ y)
    add(0b1111100, 'ANDN', 'r', 'x', 'r', lambda x, y: x & ~y)
    add(0b1010011, 'CMPEQ', 'r', 'x', 'r', lambda x, y: int(x == y))
    add(0b1010010, 'CMPEQ', 'c5', 'x', 'r', lambda x, y: int(x == y))
    add(0b1000111, 'CMPGT', 'r', 'x', 'r', lambda x, y: int(a(x) > a(y)))
    add(0b1000110, 'CMPGT', 'c5', 'x', 'r', lambda x, y: int(a(x) > a(y)))
    add(0b1001111, 'CMPGTU', 'r', 'x', 'r', lambda x, y: int(x > y))
    add(0b1001110, 'CMPGTU', 'u5', 'x', 'r', lambda x, y: int(x > y))
    add(0b1010111, 'CMPLT', 'r', 'x', 'r', lambda x, y: int(a(x) < a(y)))
    add(0b1010110, 'CMPLT', 'c5', 'x', 'r', lambda x, y: int(a(x) < a(y)))
    add(0b1011111, 'CMPLTU', 'r', 'x', 'r', lambda x, y: int(x < y))
    add(0b1011110, 'CMPLTU', 'u5', 'x', 'r', lambda x, y: int(x < y))
    add(0b1101011, 'LMBD', 'r', 'x', 'r', None)
    add(0b1101010, 'LMBD', 'c5', 'x', 'r', None)
    add(0b1100011, 'NORM', None, 'x', 'r', None)
    add(0b1000000, 'SAT', None, 'lp', 'r', None)
    add(0b0011011, 'PACKLH2', 'r', 'x', 'r', lambda x, y: (x & 0xffff) << 16 | y >> 16)
    add(0b0011110, 'PACKH2', 'r', 'x', 'r', lambda x, y: (x & 0xffff0000) | y >> 16)
    add(0b0011100, 'PACKHL2', 'r', 'x', 'r', lambda x, y: (x & 0xffff0000) | (y & 0xffff))
    add(0b1101000, 'PACKL4', 'r', 'x', 'r', lambda x, y: ((x >> 16) & 0xff) << 24 | (x & 0xff) << 16 | ((y >> 16) & 0xff) << 8 | (y & 0xff))
    add(0b1101001, 'PACKH4', 'r', 'x', 'r', lambda x, y: (x >> 24) << 24 | ((x >> 8) & 0xff) << 16 | (y >> 24) << 8 | ((y >> 8) & 0xff))
    add(0b0000000, 'PACK2', 'r', 'x', 'r', lambda x, y: (x & 0xffff) << 16 | (y & 0xffff))
    add(0b1000001, 'MIN2', 'r', 'x', 'r', None)
    add(0b1000010, 'MAX2', 'r', 'x', 'r', None)
    add(0b0010000, 'ADDSP', 'r', 'x', 'r', lambda x, y: fres(u2f(x) + u2f(y)), 3)
    add(0b0010001, 'SUBSP', 'r', 'x', 'r', lambda x, y: fres(u2f(x) - u2f(y)), 3)
    add(0b0010101, 'SUBSP', 'x', 'r', 'r', lambda x, y: fres(u2f(x) - u2f(y)), 3)
    add(0b0011000, 'ADDDP', 'dp', 'xdp', 'dp', lambda x, y: dres(u2d(x) + u2d(y)), 6)
    add(0b0011001, 'SUBDP', 'dp', 'xdp', 'dp', lambda x, y: dres(u2d(x) - u2d(y)), 6)
    add(0b0011101, 'SUBDP', 'xdp', 'dp', 'dp', lambda x, y: dres(u2d(x) - u2d(y)), 6)
    add(0b1110000, 'ADDSP', 'r', 'x', 'r', lambda x, y: fres(u2f(x) + u2f(y)), 3)       # .S unit
    add(0b1110001, 'SUBSP', 'r', 'x', 'r', lambda x, y: fres(u2f(x) - u2f(y)), 3)
    add(0b1110101, 'SUBSP', 'x', 'r', 'r', lambda x, y: fres(u2f(x) - u2f(y)), 3)
    add(0b1110010, 'ADDDP', 'dp', 'xdp', 'dp', lambda x, y: dres(u2d(x) + u2d(y)), 6)
    add(0b1110011, 'SUBDP', 'dp', 'xdp', 'dp', lambda x, y: dres(u2d(x) - u2d(y)), 6)
    add(0b1110111, 'SUBDP', 'xdp', 'dp', 'dp', lambda x, y: dres(u2d(x) - u2d(y)), 6)
    add(0b1001010, 'INTSP', None, 'x', 'r', lambda y: fres(float(s32(y))), 3)
    add(0b1001001, 'INTSPU', None, 'x', 'r', lambda y: fres(float(y)), 3)
    add(0b0001011, 'SPTRUNC', None, 'x', 'r', lambda y: sat_int(u2f(y), True), 3)
    add(0b0001010, 'SPINT', None, 'x', 'r', lambda y: sat_int(u2f(y), False), 3)
    add(0b0111001, 'INTDP', None, 'x', 'dp', lambda y: dres(float(s32(y))), 4)
    add(0b0111011, 'INTDPU', None, 'x', 'dp', lambda y: dres(float(y)), 4)
    add(0b0000001, 'DPTRUNC', None, 'dp', 'r', lambda y: sat_int(u2d(y), True), 3)
    add(0b0001000, 'DPINT', None, 'dp', 'r', lambda y: sat_int(u2d(y), False), 3)
    add(0b0001001, 'DPSP', None, 'dp', 'r', lambda y: fres(u2d(y)), 3)
    return t
L_OPS = _L_ops()

def lmbd(c, v):
    c &= 1
    for i in range(32):
        if (v >> (31 - i)) & 1 == c: return i
    return 32
def norm32(v):
    v = s32(v)
    if v == 0 or v == -1: return 31
    n = 0
    while ((v >> (30 - n)) & 1) == ((v >> 31) & 1): n += 1
    return n

# ---------------------------------------------------------------- decoder
def decode(w, addr):
    I = Ins(addr, w)
    creg, z = (w >> 29) & 7, (w >> 28) & 1
    if creg:
        if creg == 7: raise Unsupported(f'creg 7 at {addr:#x}')
        I.pred = (PRED[creg], z)
    s = (w >> 1) & 1
    dst, src2, src1, x = (w >> 23) & 31, (w >> 18) & 31, (w >> 13) & 31, (w >> 12) & 1
    lo3 = (w >> 2) & 3
    fpk = addr & ~31                              # PCE1 = fetch packet address
    # NOP / DINT / RINT
    if (w & 0x00021ffc) == 0 and (w & 0xfffc0000) in (0, 0x10000000):
        n = ((w >> 13) & 0xf) + 1
        if w & 0xfffc0000 == 0:
            if n > 9: raise Unsupported(f'IDLE at {addr:#x}')
            I.mn, I.ops, I.kind, I.nops = 'NOP', ([n] if n > 1 else []), 'nop', n - 1
            I.ex = lambda sim, R: None
            return I
        if n == 3: I.mn = 'DINT'; I.ex = lambda sim, R: sim.dint(); I.unit = ''; I.pred = None; return I
        if n == 4: I.mn = 'RINT'; I.ex = lambda sim, R: sim.rint(); I.unit = ''; I.pred = None; return I
        raise Unsupported(f'special {w:08x} at {addr:#x}')
    if lo3 == 1:
        return dec_ldst(I, w, s)
    if lo3 == 3:
        return dec_ldst15(I, w, s)
    if lo3 == 2:
        if (w >> 4) & 1: return dec_L(I, w, s, dst, src2, src1, x)
        if (w >> 5) & 1:                                                     # MVK / MVKH .S
            c = (w >> 7) & 0xffff; d = ridx(s, dst); I.unit = f'S{s + 1}'
            if (w >> 6) & 1:
                I.mn, I.ops = 'MVKH', [c << 16, rname(d)]
                I.ex = lambda sim, R: sim.w(d, (R[d] & 0xffff) | (c << 16))
            else:
                v = sx(c, 16) & M32
                I.mn, I.ops = 'MVK', [v, rname(d)]
                I.ex = lambda sim, R: sim.w(d, v)
            return I
        return dec_field(I, w, s, dst, src2)
    # lo3 == 0
    if (w >> 4) & 1:
        if (w >> 5) & 1:
            sel = (w >> 10) & 3
            if sel in (0, 1): return dec_Mx(I, w, s, dst, src2, src1, x)
            if sel == 2: return dec_Dx(I, w, s, dst, src2, src1, x)
            return dec_Sx(I, w, s, dst, src2, src1, x)
        if (w >> 6) & 1:                                                     # ADDK
            c = sx((w >> 7) & 0xffff, 16); d = ridx(s, dst)
            I.mn, I.unit, I.ops = 'ADDK', f'S{s + 1}', [c & M32, rname(d)]
            I.ex = lambda sim, R: sim.w(d, R[d] + c)
            return I
        t = (fpk + (sx((w >> 7) & 0x1fffff, 21) << 2)) & M32               # B disp / CALLP
        I.unit = f'S{s + 1}'
        if creg == 0 and z == 1:
            I.pred = None; I.mn, I.ops, I.kind = 'CALLP', [('pc', t), rname(ridx(s, 3))], 'callp'
            r = ridx(s, 3)
            I.ex = lambda sim, R: sim.callp(t, r)
        else:
            I.mn, I.ops, I.kind = 'B', [('pc', t)], 'b'
            I.ex = lambda sim, R: sim.br(t)
        return I
    if (w >> 5) & 1: return dec_S(I, w, s, dst, src2, src1, x, fpk)
    if (w >> 6) & 1: return dec_D(I, w, s, dst, src2, src1)
    return dec_M(I, w, s, dst, src2, src1, x)

def _srcs(s, x, src1, src2, k1, k2):
    """return (kind, value) operand descriptors and printed names for src1/src2"""
    def one(k, f, own):
        if k in (None,): return None
        if k == 'r': return ('r', ridx(s, f))
        if k == 'x': return ('r', ridx(s ^ x, f))
        if k == 'c5': return ('c', sx(f, 5) & M32)
        if k == 'u5': return ('c', f)
        if k in ('lp', 'lpu'): return ('p', ridx(s, f & ~1))
        if k == 'dp': return ('p', ridx(s ^ x if not own else s, f & ~1))
        if k == 'xdp': return ('p', ridx(s ^ x, f & ~1))
        raise Unsupported(k)
    return one(k1, src1, True), one(k2, src2, False)

def _pr(o):
    if o is None: return None
    k, v = o
    if k == 'r': return rname(v)
    if k == 'p': return pname(v)
    return v

def _val(R, o):
    k, v = o
    if k == 'r': return R[v]
    if k == 'p': return R[v] | R[v + 1] << 32
    return v

def _lval(R, o):
    """40-bit long operand from a pair: low 32 + 8 bits of the odd register"""
    k, v = o
    if k != 'p': return _val(R, o)
    x = R[v] | (R[v + 1] & 0xff) << 32
    return x - (1 << 40) if x >> 39 else x

def dec_L(I, w, s, dst, src2, src1, x):
    op = (w >> 5) & 0x7f
    I.unit = f'L{s + 1}' + ('X' if x else '')
    d = ridx(s, dst)
    if op == 0b0011010:                                                       # unary group
        if src1 == 0b00101:
            v = sx(src2, 5) & M32; I.unit = f'L{s + 1}'
            I.mn, I.ops = 'MVK', [v, rname(d)]
            I.ex = lambda sim, R: sim.w(d, v)
            return I
        if src1 == 0b00000:
            o2 = ridx(s ^ x, src2)
            I.mn, I.ops = 'ABS', [rname(o2), rname(d)]
            I.ex = lambda sim, R: sim.w(d, 0x7fffffff if R[o2] == 0x80000000 else abs(s32(R[o2])))
            return I
        raise Unsupported(f'.L unary {src1:05b} at {I.addr:#x}')
    if op not in L_OPS: raise Unsupported(f'.L op {op:07b} at {I.addr:#x}')
    mn, k1, k2, kd, fn, dl = L_OPS[op]
    S_FLOAT = op >> 3 == 0b1110
    if S_FLOAT: I.unit = f'S{s + 1}' + ('X' if x else '')
    if op == 0b0100001 or op == 0b0101001:      # ADD/ADDU xsint, slong: src1 field holds the long pair, src2 the int
        o1, o2 = ('r', ridx(s ^ x, src1)), ('p', ridx(s, src2 & ~1))
        I.mn, I.ops = mn, [_pr(o1), _pr(o2), pname(d)]
        signed = mn == 'ADD'
        def ex(sim, R):
            a = s32(R[o1[1]]) if signed else R[o1[1]]
            b = _lval(R, o2) if signed else (R[o2[1]] | (R[o2[1] + 1] & 0xff) << 32)
            sim.wlong(d, a + b, signed)
        I.ex = ex; return I
    if k1 == 'x':      # reversed-operand forms: the cross operand comes first (.L: src1 field, .S: src2 field)
        f1, f2 = (src2, src1) if S_FLOAT else (src1, src2)
        o1 = ('r', ridx(s ^ x, f1))
        o2 = ('r', ridx(s, f2)) if k2 == 'r' else ('p', ridx(s, f2 & ~1))
    elif k1 == 'xdp':
        f1, f2 = (src2, src1) if S_FLOAT else (src1, src2)
        o1, o2 = ('p', ridx(s ^ x, f1 & ~1)), ('p', ridx(s, f2 & ~1))
    else:
        o1, o2 = _srcs(s, x, src1, src2, k1, k2)
    if mn in ('SUBDP',) and k1 == 'xdp':
        pass
    dn = pname(d) if kd in ('l', 'ul', 'dp') else rname(d)
    ops = [p for p in (_pr(o1), _pr(o2)) if p is not None]
    # aliases used by dis6x
    if mn == 'OR' and k1 == 'c5' and o1[1] == 0: I.mn, I.ops = 'MV', [_pr(o2), dn]
    elif mn == 'XOR' and k1 == 'c5' and o1[1] == M32: I.mn, I.ops = 'NOT', [_pr(o2), dn]
    elif mn == 'SUB' and k1 == 'c5' and o1[1] == 0 and kd == 'r': I.mn, I.ops = 'NEG', [_pr(o2), dn]
    elif mn == 'ADD' and k1 == 'c5' and s32(o1[1]) < 0: I.mn, I.ops = 'SUB', [_pr(o2), (-s32(o1[1])) & M32, dn]
    elif mn in ('SUB',) and kd == 'l' and o1 == o2 or (mn == 'SUB' and kd == 'r' and k1 == 'r' and o1 == o2): I.mn, I.ops = 'ZERO', [dn]
    elif mn in ('ADD', 'AND', 'OR', 'XOR', 'CMPEQ', 'CMPGT', 'CMPGTU', 'CMPLT', 'CMPLTU', 'LMBD', 'SUB') and k1 in ('c5', 'u5'):
        I.mn, I.ops = mn, [o1[1], _pr(o2), dn]
    else:
        I.mn, I.ops = mn, ops + [dn]
    if mn == 'SUBC':
        def ex(sim, R):
            a, b = R[o1[1]], R[o2[1]]
            t = (a - b) & M32
            sim.w(d, ((t << 1) + 1) & M32 if a >= b else (a << 1) & M32)
    elif mn == 'LMBD':
        ex = lambda sim, R: sim.w(d, lmbd(_val(R, o1), _val(R, o2)))
    elif mn == 'NORM':
        ex = lambda sim, R: sim.w(d, norm32(_val(R, o2)))
    elif mn == 'SAT':
        def ex(sim, R):
            v = _lval(R, o2); sim.w(d, 0x7fffffff if v > 0x7fffffff else 0x80000000 if v < -0x80000000 else v & M32)
    elif mn in ('MIN2', 'MAX2'):
        f = min if mn == 'MIN2' else max
        def ex(sim, R):
            a, b = _val(R, o1), _val(R, o2)
            sim.w(d, (f(sx(a, 16), sx(b, 16)) & 0xffff) | (f(sx(a >> 16, 16), sx(b >> 16, 16)) & 0xffff) << 16)
    elif kd == 'l' or kd == 'ul':
        signed = kd == 'l'
        def ex(sim, R):
            a = _lval(R, o1) if o1[0] == 'p' else _val(R, o1)
            b = _lval(R, o2) if o2[0] == 'p' else _val(R, o2)
            if not signed:
                a = _val(R, o1) & ((1 << 40) - 1) if o1[0] == 'p' else _val(R, o1)
                b = (R[o2[1]] | (R[o2[1] + 1] & 0xff) << 32) if o2[0] == 'p' else _val(R, o2)
            sim.wlong(d, fn(a, b), signed, dl)
    elif kd == 'dp':
        if o1 is None:     # INTDP/INTDPU
            ex = dp_ex([(o2[1], False, None)], fn, d, 'dp', dl)
        else:              # ADDDP/SUBDP: high source words read in E2
            ex = dp_ex([(o1[1], True, 1), (o2[1], True, 1)], fn, d, 'dp', dl)
    elif o1 is None:
        ex = lambda sim, R: sim.w(d, fn(_val(R, o2)), dl)
    else:
        ex = lambda sim, R: sim.w(d, fn(_val(R, o1), _val(R, o2)), dl)
    I.ex = ex
    return I

# ---------------------------------------------------------------- .S unit (classic format)
def shl(a, n): return (a << n) & M32 if n < 32 else 0
def shr(a, n): return (s32(a) >> min(n, 31)) & M32
def shru(a, n): return a >> n if n < 32 else 0
def sshl(a, n):
    v = s32(a) << n
    return 0x7fffffff if v > 0x7fffffff else 0x80000000 if v < -0x80000000 else v & M32

S_OPS = {
    0b000111: ('ADD', 'r', 'x', lambda a, b: a + b),
    0b000110: ('ADD', 'c5', 'x', lambda a, b: a + b),
    0b010111: ('SUB', 'r', 'x', lambda a, b: a - b),
    0b010110: ('SUB', 'c5', 'x', lambda a, b: a - b),
    0b011111: ('AND', 'r', 'x', lambda a, b: a & b),
    0b011110: ('AND', 'c5', 'x', lambda a, b: a & b),
    0b011011: ('OR', 'r', 'x', lambda a, b: a | b),
    0b011010: ('OR', 'c5', 'x', lambda a, b: a | b),
    0b001011: ('XOR', 'r', 'x', lambda a, b: a ^ b),
    0b001010: ('XOR', 'c5', 'x', lambda a, b: a ^ b),
    0b110011: ('SHL', 'r6', 'x', lambda n, a: shl(a, n)),
    0b110010: ('SHL', 'u5', 'x', lambda n, a: shl(a, n)),
    0b110111: ('SHR', 'r6', 'x', lambda n, a: shr(a, n)),
    0b110110: ('SHR', 'u5', 'x', lambda n, a: shr(a, n)),
    0b100111: ('SHRU', 'r6', 'x', lambda n, a: shru(a, n)),
    0b100110: ('SHRU', 'u5', 'x', lambda n, a: shru(a, n)),
    0b100011: ('SSHL', 'r6', 'x', lambda n, a: sshl(a, n)),
    0b100010: ('SSHL', 'u5', 'x', lambda n, a: sshl(a, n)),
    0b101011: ('EXTU', 'r', 'x', None), 0b101111: ('EXT', 'r', 'x', None),
    0b111011: ('SET', 'r', 'x', None), 0b111111: ('CLR', 'r', 'x', None),
    0b000001: ('ADD2', 'r', 'x', lambda a, b: ((a + b) & 0xffff) | (((a >> 16) + (b >> 16)) & 0xffff) << 16),
    0b010001: ('SUB2', 'r', 'x', lambda a, b: ((a - b) & 0xffff) | (((a >> 16) - (b >> 16)) & 0xffff) << 16),
    0b111000: ('CMPEQSP', 'r', 'x', lambda a, b: int(u2f(a) == u2f(b))),
    0b111001: ('CMPGTSP', 'r', 'x', lambda a, b: int(u2f(a) > u2f(b))),
    0b111010: ('CMPLTSP', 'r', 'x', lambda a, b: int(u2f(a) < u2f(b))),
    0b101000: ('CMPEQDP', 'dp', 'xdp', lambda a, b: int(u2d(a) == u2d(b)), 1),
    0b101001: ('CMPGTDP', 'dp', 'xdp', lambda a, b: int(u2d(a) > u2d(b)), 1),
    0b101010: ('CMPLTDP', 'dp', 'xdp', lambda a, b: int(u2d(a) < u2d(b)), 1),
}

def _rcp_approx(v, bits):
    """reciprocal with an 8-bit mantissa (RCPSP/RCPDP)"""
    if v == 0: return math.copysign(math.inf, v)
    if math.isinf(v): return math.copysign(0.0, v)
    m, e = math.frexp(1.0 / v)
    m = math.copysign(math.floor(abs(m) * 512) / 512, m)
    return math.ldexp(m, e)

def dec_S(I, w, s, dst, src2, src1, x, fpk):
    op = (w >> 6) & 0x3f
    I.unit = f'S{s + 1}' + ('X' if x else '')
    d = ridx(s, dst)
    low = w & 0x1ffc
    if low == 0x0120:                                                         # BNOP disp,n
        t = (fpk + (sx((w >> 16) & 0xfff, 12) << 2)) & M32; n = (w >> 13) & 7
        I.unit = f'S{s + 1}'; I.mn, I.ops, I.kind, I.nops = 'BNOP', [('pc', t), n], 'b', n
        I.ex = lambda sim, R: sim.br(t)
        return I
    if low == 0x0160:                                                         # ADDKPC
        t = (fpk + (sx((w >> 16) & 0x7f, 7) << 2)) & M32; n = (w >> 13) & 7
        I.unit = f'S{s + 1}'; I.mn, I.ops, I.nops = 'ADDKPC', [('pc', t), rname(d), n], n
        I.ex = lambda sim, R: sim.w(d, t)
        return I
    if low in (0x1020, 0x0020):                                               # BDEC / BPOS
        t = (fpk + (sx((w >> 13) & 0x3ff, 10) << 2)) & M32
        I.unit = f'S{s + 1}'
        if low == 0x1020:
            I.mn, I.ops, I.kind = 'BDEC', [('pc', t), rname(d)], 'b'
            def ex(sim, R):
                if s32(R[d]) >= 0: sim.br(t); sim.w(d, R[d] - 1)
        else:
            I.mn, I.ops, I.kind = 'BPOS', [('pc', t), rname(d)], 'b'
            def ex(sim, R):
                if s32(R[d]) >= 0: sim.br(t)
        I.ex = ex
        return I
    if op == 0b000011 and src2 in (6, 7):                                     # B IRP / B NRP
        I.unit = 'S2'; I.mn, I.ops, I.kind = 'B', ['IRP' if src2 == 6 else 'NRP'], 'b'
        I.ex = lambda sim, R: sim.b_irp(src2)
        return I
    if op == 0b001101:                                                        # B reg / BNOP reg
        r = ridx(s ^ x, src2); I.unit = 'S2' + ('X' if x else '')
        if dst == 0 and src1 == 0:
            I.mn, I.ops, I.kind = 'B', [rname(r)], 'b'
            I.ex = lambda sim, R: sim.br(R[r])
        else:
            n = (w >> 13) & 7
            I.mn, I.ops, I.kind, I.nops = 'BNOP', [rname(r), n], 'b', n
            I.ex = lambda sim, R: sim.br(R[r])
        return I
    if op in (0b001110, 0b001111):                                            # MVC
        I.unit = 'S2'
        if op == 0b001110:
            r = ridx(1 ^ x, src2); cr = dst
            I.unit = 'S2' + ('X' if x else '')
            I.mn, I.ops = 'MVC', [rname(r), CTRL.get(cr, f'CR{cr}')]
            I.ex = lambda sim, R: sim.mvc_to(cr, R[r])
        else:
            cr = src2; dd = ridx(1, dst)
            I.mn, I.ops = 'MVC', [CTRL.get(cr, f'CR{cr}'), rname(dd)]
            I.ex = lambda sim, R: sim.w(dd, sim.mvc_from(cr))
        return I
    if op == 0b000010:                                                        # SPDP
        o2 = ridx(s ^ x, src2)
        I.mn, I.ops = 'SPDP', [rname(o2), pname(d)]
        I.ex = dp_ex([(o2, False, None)], lambda v: dres(u2f(v)), d, 'dp', 1)
        return I
    if op in (0b101101, 0b111101):                                            # RCPDP / RCPSP
        if op == 0b101101:
            o2 = ridx(s ^ x, src2 & ~1)
            I.mn, I.ops = 'RCPDP', [pname(o2), pname(d)]
            I.ex = dp_ex([(o2, True, None)], lambda v: dres(_rcp_approx(u2d(v), 8)) & 0xffffffff00000000, d, 'dp', 1)
        else:
            o2 = ridx(s ^ x, src2)
            I.mn, I.ops = 'RCPSP', [rname(o2), rname(d)]
            I.ex = lambda sim, R: sim.w(d, fres(_rcp_approx(u2f(R[o2]), 8)))
        return I
    if op in (0b111110, 0b101110):                                            # RSQRSP / RSQRDP (8-bit estimate)
        o2 = ridx(s ^ x, src2)
        def rsqr(v): return _rcp_approx(math.sqrt(v), 8) if v > 0 else (math.inf if v == 0 else math.nan)
        if op == 0b111110:
            I.mn, I.ops = 'RSQRSP', [rname(o2), rname(d)]
            I.ex = lambda sim, R: sim.w(d, fres(rsqr(u2f(R[o2]))))
        else:
            o2 &= ~1
            I.mn, I.ops = 'RSQRDP', [pname(o2), pname(d)]
            I.ex = dp_ex([(o2, True, None)], lambda v: dres(rsqr(u2d(v))) & 0xffffffff00000000, d, 'dp', 1)
        return I
    if op in (0b101100, 0b111100):                                            # ABSDP / ABSSP
        o2 = ridx(s ^ x, src2)
        if op == 0b111100:
            I.mn, I.ops = 'ABSSP', [rname(o2), rname(d)]
            I.ex = lambda sim, R: sim.w(d, R[o2] & 0x7fffffff if R[o2] & 0x7f800000 else 0)
        else:
            o2 &= ~1
            I.mn, I.ops = 'ABSDP', [pname(o2), pname(d)]
            I.ex = dp_ex([(o2, True, None)], lambda v: v & 0x7fffffffffffffff, d, 'dp', 1)
        return I
    ent = S_OPS.get(op)
    if not ent: raise Unsupported(f'.S op {op:06b} at {I.addr:#x}')
    mn, k1, k2, fn = ent[:4]; dl = ent[4] if len(ent) > 4 else 0
    if k1 == 'dp':
        o1, o2 = ridx(s, src1 & ~1), ridx(s ^ x, src2 & ~1)
        I.mn, I.ops = mn, [pname(o1), pname(o2), rname(d)]
        I.ex = dp_ex([(o1, True, 1), (o2, True, 1)], fn, d, 'r', dl)
        return I
    o2 = ridx(s ^ x, src2)
    if mn in ('EXTU', 'EXT', 'SET', 'CLR'):
        o1 = ridx(s, src1)
        I.mn, I.ops = mn, [rname(o2), rname(o1), rname(d)]
        I.ex = lambda sim, R: sim.w(d, field_op(mn, R[o2], (R[o1] >> 5) & 31, R[o1] & 31))
        return I
    if k1 == 'r' or k1 == 'r6':
        o1 = ridx(s, src1)
        if k1 == 'r6':       # shifts: printed src2,src1,dst; amount = 6 LSBs of src1 (>39 -> 40)
            I.mn, I.ops = mn, [rname(o2), rname(o1), rname(d)]
            I.ex = lambda sim, R: sim.w(d, fn(min(R[o1] & 0x3f, 40), R[o2]))
        else:
            I.mn, I.ops = mn, [rname(o1), rname(o2), rname(d)]
            I.ex = lambda sim, R: sim.w(d, fn(R[o1], R[o2]))
        return I
    c = sx(src1, 5) & M32 if k1 == 'c5' else src1
    if k1 == 'u5':
        I.mn, I.ops = mn, [rname(o2), c, rname(d)]
        I.ex = lambda sim, R: sim.w(d, fn(c, R[o2]))
        return I
    if mn == 'OR' and c == 0: I.mn, I.ops = 'MV', [rname(o2), rname(d)]
    elif mn == 'XOR' and c == M32: I.mn, I.ops = 'NOT', [rname(o2), rname(d)]
    elif mn == 'SUB' and c == 0: I.mn, I.ops = 'NEG', [rname(o2), rname(d)]
    elif mn == 'ADD' and s32(c) < 0: I.mn, I.ops = 'SUB', [rname(o2), (-s32(c)) & M32, rname(d)]
    else: I.mn, I.ops = mn, [c, rname(o2), rname(d)]
    I.ex = lambda sim, R: sim.w(d, fn(c, R[o2]))
    return I

CTRL = {0: 'AMR', 1: 'CSR', 2: 'IFR', 3: 'ICR', 4: 'IER', 5: 'ISTP', 6: 'IRP', 7: 'NRP', 10: 'TSCL', 11: 'TSCH',
        13: 'ILC', 14: 'RILC', 15: 'REP', 16: 'PCE1', 17: 'DNUM', 18: 'FADCR', 19: 'FAUCR', 20: 'FMCR', 21: 'SSR',
        24: 'GFPGFR', 27: 'ITSR', 28: 'NTSR', 29: 'ECR'}

def field_op(mn, v, a, b):
    if mn == 'EXTU': return ((v << a) & M32) >> b
    if mn == 'EXT': return (s32((v << a) & M32) >> b) & M32
    if b < a: return v
    m = ((1 << (b - a + 1)) - 1) << a
    return (v | m) & M32 if mn == 'SET' else v & ~m & M32

def dec_field(I, w, s, dst, src2):
    op = (w >> 6) & 3; a = (w >> 13) & 31; b = (w >> 8) & 31
    mn = ('EXTU', 'EXT', 'SET', 'CLR')[op]
    I.unit = f'S{s + 1}'
    o2, d = ridx(s, src2), ridx(s, dst)
    I.mn, I.ops = mn, [rname(o2), a, b, rname(d)]
    I.ex = lambda sim, R: sim.w(d, field_op(mn, R[o2], a, b))
    return I

# ---------------------------------------------------------------- .S extended (bits 11-10 = 11)
SX_OPS = {
    0b0001: ('ADD2', lambda a, b: ((a + b) & 0xffff) | (((a >> 16) + (b >> 16)) & 0xffff) << 16),
    0b0010: ('SUB2', lambda a, b: ((a - b) & 0xffff) | (((a >> 16) - (b >> 16)) & 0xffff) << 16),
    0b0110: ('ANDN', lambda a, b: a & ~b & M32),
    0b0101: ('SUB', lambda a, b: a - b, 'rev'),
    0b1111: ('PACK2', lambda a, b: (a & 0xffff) << 16 | (b & 0xffff)),
}
def dec_Sx(I, w, s, dst, src2, src1, x):
    op = (w >> 6) & 0xf
    if op not in SX_OPS: raise Unsupported(f'.S ext op {op:04b} at {I.addr:#x}')
    mn, fn = SX_OPS[op][:2]
    o1, o2, d = ridx(s, src1), ridx(s ^ x, src2), ridx(s, dst)
    if len(SX_OPS[op]) > 2: o1, o2 = ridx(s ^ x, src2), ridx(s, src1)
    I.unit = f'S{s + 1}' + ('X' if x else '')
    I.mn, I.ops = mn, [rname(o1), rname(o2), rname(d)]
    I.ex = lambda sim, R: sim.w(d, fn(R[o1], R[o2]))
    return I

# ---------------------------------------------------------------- .D unit
def dec_D(I, w, s, dst, src2, src1):
    op = (w >> 7) & 0x3f
    I.unit = f'D{s + 1}'
    d, o2 = ridx(s, dst), ridx(s, src2)
    if op == 0b000000:
        v = sx(src1, 5) & M32
        I.mn, I.ops = 'MVK', [v, rname(d)]
        I.ex = lambda sim, R: sim.w(d, v)
        return I
    tab = {0b010000: ('ADD', 'r', 0), 0b010010: ('ADD', 'u', 0), 0b010001: ('SUB', 'r', 0), 0b010011: ('SUB', 'u', 0),
           0b110000: ('ADDAB', 'r', 0), 0b110010: ('ADDAB', 'u', 0), 0b110100: ('ADDAH', 'r', 1), 0b110110: ('ADDAH', 'u', 1),
           0b111000: ('ADDAW', 'r', 2), 0b111010: ('ADDAW', 'u', 2), 0b110001: ('SUBAB', 'r', 0), 0b110011: ('SUBAB', 'u', 0),
           0b110101: ('SUBAH', 'r', 1), 0b110111: ('SUBAH', 'u', 1), 0b111001: ('SUBAW', 'r', 2), 0b111011: ('SUBAW', 'u', 2),
           0b111100: ('ADDAD', 'r', 3), 0b111101: ('ADDAD', 'u', 3)}
    if op not in tab: raise Unsupported(f'.D op {op:06b} at {I.addr:#x}')
    mn, k, sh = tab[op]
    sub = mn.startswith('SUB')
    if k == 'r':
        o1 = ridx(s, src1)
        I.mn, I.ops = mn, [rname(o2), rname(o1), rname(d)]
        I.ex = lambda sim, R: sim.w(d, R[o2] - (R[o1] << sh) if sub else R[o2] + (R[o1] << sh))
    else:
        c = src1
        if mn == 'ADD' and c == 0: I.mn, I.ops = 'MV', [rname(o2), rname(d)]
        else: I.mn, I.ops = mn, [rname(o2), c, rname(d)]
        I.ex = lambda sim, R: sim.w(d, R[o2] - (c << sh) if sub else R[o2] + (c << sh))
    return I

DX_OPS = {0b1010: ('ADD', 'r', lambda a, b: a + b), 0b1011: ('ADD', 'c5', lambda a, b: a + b),
          0b1100: ('SUB', 'r', lambda a, b: a - b), 0b1101: ('SUB', 'c5', lambda a, b: a - b),
          0b0010: ('OR', 'r', lambda a, b: a | b), 0b0011: ('OR', 'c5', lambda a, b: a | b),
          0b0110: ('AND', 'r', lambda a, b: a & b), 0b0111: ('AND', 'c5', lambda a, b: a & b),
          0b1110: ('XOR', 'r', lambda a, b: a ^ b), 0b1111: ('XOR', 'c5', lambda a, b: a ^ b),
          0b0000: ('ANDN', 'r', lambda a, b: a & ~b), 0b0100: ('ADD2', 'r', None), 0b0101: ('SUB2', 'r', None)}
def dec_Dx(I, w, s, dst, src2, src1, x):
    op = (w >> 6) & 0xf
    if op not in DX_OPS: raise Unsupported(f'.D ext op {op:04b} at {I.addr:#x}')
    mn, k, fn = DX_OPS[op]
    if fn is None: fn = SX_OPS[0b0001 if mn == 'ADD2' else 0b0010][1]
    I.unit = f'D{s + 1}' + ('X' if x else '')
    o2, d = ridx(s ^ x, src2), ridx(s, dst)
    if k == 'r':
        o1 = ridx(s, src1)
        I.mn, I.ops = mn, [rname(o1), rname(o2), rname(d)]
        I.ex = lambda sim, R: sim.w(d, fn(R[o1], R[o2]))
    else:
        c = sx(src1, 5) & M32
        if mn == 'OR' and c == 0: I.mn, I.ops = 'MV', [rname(o2), rname(d)]
        elif mn == 'ADD' and s32(c) < 0: I.mn, I.ops = 'SUB', [rname(o2), (-s32(c)) & M32, rname(d)]
        elif mn == 'XOR' and c == M32: I.mn, I.ops = 'NOT', [rname(o2), rname(d)]
        elif mn == 'SUB' and c == 0: I.mn, I.ops = 'NEG', [rname(o2), rname(d)]
        else: I.mn, I.ops = mn, [c, rname(o2), rname(d)]
        I.ex = lambda sim, R: sim.w(d, fn(c, R[o2]))
    return I

# ---------------------------------------------------------------- .M unit
def h(v, hi, signed):
    v = (v >> 16) if hi else v & 0xffff
    return sx(v, 16) if signed else v & 0xffff

M_OPS = {   # op (bits 11-7): (mn, src1 kind, result fn(a, b), delay, dst kind)
    0b11001: ('MPY', 'r', lambda a, b: h(a, 0, 1) * h(b, 0, 1), 1, 'r'),
    0b11000: ('MPY', 'c5', lambda a, b: s32(a) * h(b, 0, 1), 1, 'r'),
    0b11111: ('MPYU', 'r', lambda a, b: h(a, 0, 0) * h(b, 0, 0), 1, 'r'),
    0b11101: ('MPYUS', 'r', lambda a, b: h(a, 0, 0) * h(b, 0, 1), 1, 'r'),
    0b11011: ('MPYSU', 'r', lambda a, b: h(a, 0, 1) * h(b, 0, 0), 1, 'r'),
    0b11110: ('MPYSU', 'c5', lambda a, b: s32(a) * h(b, 0, 0), 1, 'r'),
    0b00001: ('MPYH', 'r', lambda a, b: h(a, 1, 1) * h(b, 1, 1), 1, 'r'),
    0b00111: ('MPYHU', 'r', lambda a, b: h(a, 1, 0) * h(b, 1, 0), 1, 'r'),
    0b00101: ('MPYHUS', 'r', lambda a, b: h(a, 1, 0) * h(b, 1, 1), 1, 'r'),
    0b00011: ('MPYHSU', 'r', lambda a, b: h(a, 1, 1) * h(b, 1, 0), 1, 'r'),
    0b01001: ('MPYHL', 'r', lambda a, b: h(a, 1, 1) * h(b, 0, 1), 1, 'r'),
    0b01111: ('MPYHLU', 'r', lambda a, b: h(a, 1, 0) * h(b, 0, 0), 1, 'r'),
    0b10001: ('MPYLH', 'r', lambda a, b: h(a, 0, 1) * h(b, 1, 1), 1, 'r'),
    0b10111: ('MPYLHU', 'r', lambda a, b: h(a, 0, 0) * h(b, 1, 0), 1, 'r'),
    0b10000: ('MPY32', 'r', lambda a, b: s32(a) * s32(b), 3, 'r'),
    0b10100: ('MPY32', 'r', lambda a, b: s32(a) * s32(b), 3, 'p'),
    0b10110: ('MPY32SU', 'r', lambda a, b: s32(a) * b, 3, 'p'),
    0b11100: ('MPYSP', 'r', lambda a, b: fres(u2f(a) * u2f(b)), 3, 'f'),
    0b01110: ('MPYDP', 'dp', lambda a, b: dres(u2d(a) * u2d(b)), 9, 'dp'),
    0b10010: ('MPYSPDP', 'r', None, 6, 'dp'),
}
MX_OPS = {   # extended .M (bits 10-6)
    0b11000: ('MPY32U', lambda a, b: a * b, 3, 'p'),
    0b11001: ('MPY32US', lambda a, b: a * s32(b), 3, 'p'),
    0b10101: ('MPYLI', lambda a, b: h(a, 0, 1) * s32(b), 3, 'p'),
    0b10100: ('MPYHI', lambda a, b: h(a, 1, 1) * s32(b), 3, 'p'),
    0b11101: ('ROTL', None, 1, 'r'), 0b11110: ('ROTL', None, 1, 'rc'),
    0b00011: ('MVD', None, 3, 'mvd'),
    0b00001: ('BITC4', None, 1, 'r'),
    0b10110: ('MPYSPDP', None, 6, 'spdp'), 0b10111: ('MPYSP2DP', None, 4, 'sp2dp'),
}

def dec_M(I, w, s, dst, src2, src1, x):
    op = (w >> 7) & 0x1f
    if op not in M_OPS: raise Unsupported(f'.M op {op:05b} at {I.addr:#x}')
    mn, k1, fn, dl, kd = M_OPS[op]
    I.unit = f'M{s + 1}' + ('X' if x else '')
    d = ridx(s, dst)
    if k1 == 'dp':
        o1, o2 = ridx(s, src1), ridx(s ^ x, src2)
        I.mn, I.ops = mn, [pname(o1), pname(o2), pname(d)]
        I.ex = dp_ex([(o1, True, 2), (o2, True, 1)], fn, d, 'dp', dl)
        return I
    o2 = ridx(s ^ x, src2)
    if k1 == 'c5':
        c = sx(src1, 5)
        I.mn, I.ops = mn, [c & M32, rname(o2), rname(d)]
        I.ex = lambda sim, R: sim.w(d, fn(c, R[o2]) & M32, dl)
        return I
    o1 = ridx(s, src1)
    if kd == 'p':
        I.mn, I.ops = mn, [rname(o1), rname(o2), pname(d)]
        I.ex = lambda sim, R: sim.w64(d, fn(R[o1], R[o2]) & (2 ** 64 - 1), dl)
    else:
        I.mn, I.ops = mn, [rname(o1), rname(o2), rname(d)]
        I.ex = lambda sim, R: sim.w(d, fn(R[o1], R[o2]) & M32, dl)
    return I

def dec_Mx(I, w, s, dst, src2, src1, x):
    op = (w >> 6) & 0x1f
    if op not in MX_OPS: raise Unsupported(f'.M ext op {op:05b} at {I.addr:#x}')
    mn, fn, dl, kd = MX_OPS[op]
    I.unit = f'M{s + 1}' + ('X' if x else '')
    d, o2 = ridx(s, dst), ridx(s ^ x, src2)
    if kd == 'mvd':
        I.mn, I.ops = mn, [rname(o2), rname(d)]
        I.ex = lambda sim, R: sim.w(d, R[o2], dl)
        return I
    if kd == 'spdp':
        o1, o2 = ridx(s, src1), ridx(s ^ x, src2 & ~1)
        I.mn, I.ops = mn, [rname(o1), pname(o2), pname(d)]
        I.ex = dp_ex([(o1, False, None), (o2, True, 1)], lambda a, b: dres(u2f(a) * u2d(b)), d, 'dp', dl)
        return I
    if kd == 'sp2dp':
        o1 = ridx(s, src1)
        I.mn, I.ops = mn, [rname(o1), rname(o2), pname(d)]
        I.ex = dp_ex([(o1, False, None), (o2, False, None)], lambda a, b: dres(u2f(a) * u2f(b)), d, 'dp', dl)
        return I
    if mn == 'ROTL':
        if kd == 'rc':
            c = src1
            I.mn, I.ops = mn, [rname(o2), c, rname(d)]
            I.ex = lambda sim, R: sim.w(d, ((R[o2] << c) | (R[o2] >> (32 - c))) & M32 if c else R[o2], dl)
        else:
            o1 = ridx(s, src1)
            I.mn, I.ops = mn, [rname(o2), rname(o1), rname(d)]
            def ex(sim, R):
                c = R[o1] & 31; sim.w(d, ((R[o2] << c) | (R[o2] >> (32 - c))) & M32 if c else R[o2], dl)
            I.ex = ex
        return I
    if fn is None: raise Unsupported(f'{mn} at {I.addr:#x}')
    o1 = ridx(s, src1)
    I.mn, I.ops = mn, [rname(o1), rname(o2), pname(d)]
    I.ex = lambda sim, R: sim.w64(d, fn(R[o1], R[o2]) & (2 ** 64 - 1), dl)
    return I

# ---------------------------------------------------------------- loads / stores
LDST = {  # (r bit, op) -> mn, size, signed, is_store
    (0, 0): ('LDHU', 2, 0, 0), (0, 1): ('LDBU', 1, 0, 0), (0, 2): ('LDB', 1, 1, 0), (0, 3): ('STB', 1, 0, 1),
    (0, 4): ('LDH', 2, 1, 0), (0, 5): ('STH', 2, 0, 1), (0, 6): ('LDW', 4, 0, 0), (0, 7): ('STW', 4, 0, 1),
    (1, 6): ('LDDW', 8, 0, 0), (1, 4): ('STDW', 8, 0, 1), (1, 2): ('LDNDW', 8, 0, 0), (1, 7): ('STNDW', 8, 0, 1),
    (1, 3): ('LDNW', 4, 0, 0), (1, 5): ('STNW', 4, 0, 1),
}
MODES = {0b0000: ('-', '', 'c'), 0b0001: ('+', '', 'c'), 0b0100: ('-', '', 'r'), 0b0101: ('+', '', 'r'),
         0b1000: ('--', '', 'c'), 0b1001: ('++', '', 'c'), 0b1010: ('', '--', 'c'), 0b1011: ('', '++', 'c'),
         0b1100: ('--', '', 'r'), 0b1101: ('++', '', 'r'), 0b1110: ('', '--', 'r'), 0b1111: ('', '++', 'r')}

def dec_ldst(I, w, s):
    op, y, r, mode = (w >> 4) & 7, (w >> 7) & 1, (w >> 8) & 1, (w >> 9) & 0xf
    if (r, op) not in LDST: raise Unsupported(f'ldst r{r} op{op} at {I.addr:#x}')
    mn, size, signed, st = LDST[(r, op)]
    if mode not in MODES: raise Unsupported(f'ldst mode {mode:04b} at {I.addr:#x}')
    pre, post, ok = MODES[mode]
    base = ridx(y, (w >> 18) & 31); off = (w >> 13) & 31
    dreg = ridx(s, (w >> 23) & 31)
    scale = size
    if mn in ('LDNDW', 'STNDW'):          # dst is 4 bits (even pair), bit 23 = sc (scale constant/register by 8)
        dreg = ridx(s, (w >> 23) & 0x1e); sc = (w >> 23) & 1
        scale = 8 if sc else 1
        if ok == 'c' and not sc: pass
    I.unit = f'D{y + 1}T{s + 1}'
    offr = ridx(y, off) if ok == 'r' else None
    offtxt = rname(offr) if ok == 'r' else off
    br = '[' if (scale == size or mn in ('LDNDW', 'STNDW') and scale == 8) else '('
    memop = ('mem', pre, rname(base), post, br, offtxt)
    dn = pname(dreg) if size == 8 else rname(dreg)
    I.mn = mn; I.ops = [dn, memop] if st else [memop, dn]
    I.kind = 'st' if st else 'ld'
    def ex(sim, R):
        b = R[base]; o = (R[offr] if offr is not None else off) * scale
        if pre in ('+', '++'): ea = b + o
        elif pre in ('-', '--'): ea = b - o
        else: ea = b
        ea &= M32
        if pre in ('++', '--'): sim.w(base, ea)
        elif post == '++': sim.w(base, b + o)
        elif post == '--': sim.w(base, b - o)
        if st:
            v = R[dreg] | (R[dreg + 1] << 32 if size == 8 else 0)
            sim.mem(lambda: sim.store(ea, v, size, mn))
        else:
            sim.mem(lambda: sim.load(ea, dreg, size, signed, mn))
    I.ex = ex
    return I

def dec_ldst15(I, w, s):
    op, y = (w >> 4) & 7, (w >> 7) & 1
    c = (w >> 8) & 0x7fff
    base = ridx(1, 15 if y else 14)
    d = ridx(s, (w >> 23) & 31)
    if (w >> 28) == 1:                    # creg 0, z 1: ADDAB/ADDAH/ADDAW B14/B15, ucst15, dst
        sh = {3: 0, 5: 1, 7: 2}.get(op)
        if sh is None: raise Unsupported(f'adda15 op{op} at {I.addr:#x}')
        I.pred = None; I.unit = 'D2'
        I.mn, I.ops = ('ADDAB', 'ADDAH', 'ADDAW')[sh], [rname(base), c, rname(d)]
        I.ex = lambda sim, R: sim.w(d, R[base] + (c << sh))
        return I
    if (0, op) not in LDST: raise Unsupported(f'ld15 op{op} at {I.addr:#x}')
    mn, size, signed, st = LDST[(0, op)]
    I.unit = f'D2T{s + 1}'
    I.mn = mn
    memop = ('mem', '+', rname(base), '', '[', c)
    I.ops = [rname(d), memop] if st else [memop, rname(d)]
    I.kind = 'st' if st else 'ld'
    def ex(sim, R):
        ea = (R[base] + c * size) & M32
        if st:
            v = R[d]; sim.mem(lambda: sim.store(ea, v, size, mn))
        else:
            sim.mem(lambda: sim.load(ea, d, size, signed, mn))
    I.ex = ex
    return I

# ---------------------------------------------------------------- simulator
class Sim:
    PAGE = 1 << 16
    CALLER_SAVED = [i for i in range(64) if (i & 31) < 10 or (i & 31) >= 16]

    def __init__(self):
        self.pages = {}
        self.R = [0] * 64
        self.cycle = 0
        self.code = {}            # addr -> Ins (decoded lazily from memory)
        self.mocks = {}           # addr -> fn(sim) ; called with args in A4,B4,A6,...; return value in A4
        self.mock_clobber = 0xDEADBEEF
        self.pend = []            # (cycle_visible, kind, data)
        self.branches = []        # in-flight branches: (cycle, target); pipelined loops keep several
        self.gie = 1
        self.trace = None
        self.ninstr = 0
        self.calls = []
        self.io = {}              # 4 KB page -> handler(sim, addr, size, value or None for a read)
        self.tscl_scale = 1
        # interrupts: IFR/IER/ISTP/IRP and CSR.PGIE; a peripheral model raises one with raise_int(n). Board models
        # that need time-based events set next_event (cycle) and on_event(sim) (it returns the next cycle).
        self.ifr = 0; self.ier = 0; self.pgie = 0; self.ints_taken = 0
        self.next_event = None; self.on_event = None

    # memory
    def _pg(self, a):
        p = self.pages.get(a >> 16)
        if p is None: p = self.pages[a >> 16] = bytearray(self.PAGE)
        return p
    def write(self, a, data):
        for i, b in enumerate(data): self._pg(a + i)[(a + i) & 0xffff] = b
        for k in list(self.code):
            if a <= k < a + len(data): del self.code[k]
    def read(self, a, n): return bytes(self._pg(a + i)[(a + i) & 0xffff] for i in range(n))
    def rd32(self, a):
        if a & 0xffff <= 0xfffc:
            p = self._pg(a); o = a & 0xffff
            return p[o] | p[o + 1] << 8 | p[o + 2] << 16 | p[o + 3] << 24
        return int.from_bytes(self.read(a, 4), 'little')
    def wr32(self, a, v): self.write(a, (v & M32).to_bytes(4, 'little'))
    def cstr(self, a):
        b = bytearray()
        while True:
            c = self._pg(a)[a & 0xffff]
            if not c: return b.decode('latin1')
            b.append(c); a += 1

    def store(self, ea, v, size, mn):
        h = self.io.get(ea >> 12) if self.io else None
        if h: h(self, ea, size, v & ((1 << (8 * size)) - 1)); return
        if size > 1 and mn not in ('STNW', 'STNDW') and ea % size:
            raise RuntimeError(f'misaligned {mn} at {ea:#x}')
        p = self._pg(ea); o = ea & 0xffff
        if o + size <= 0x10000:
            for i in range(size): p[o + i] = (v >> (8 * i)) & 0xff
        else:
            self.write(ea, (v & ((1 << (8 * size)) - 1)).to_bytes(size, 'little'))
    def load(self, ea, d, size, signed, mn):
        if size > 1 and mn not in ('LDNW', 'LDNDW') and ea % size:
            raise RuntimeError(f'misaligned {mn} at {ea:#x}')
        h = self.io.get(ea >> 12) if self.io else None
        p = self._pg(ea); o = ea & 0xffff
        if h: v = h(self, ea, size, None)
        elif o + size <= 0x10000: v = int.from_bytes(p[o:o + size], 'little')
        else: v = int.from_bytes(self.read(ea, size), 'little')
        if size == 8: self._q(self.cycle + 3, d, v & M32); self._q(self.cycle + 3, d + 1, v >> 32)
        else:
            if signed: v = sx(v, 8 * size) & M32
            self._q(self.cycle + 3, d, v)   # mem() runs at issue+2; register visible at issue+5

    # pipeline plumbing used by Ins.ex
    def _q(self, cyc, reg, v): self.pend.append((cyc, reg, v & M32))
    def w(self, reg, v, delay=0): self.pend.append((self.cycle + delay + 1, reg, v & M32))
    def w64(self, reg, v, delay=0):
        self.pend.append((self.cycle + delay + 1, reg, v & M32)); self.pend.append((self.cycle + delay + 1, reg + 1, (v >> 32) & M32))
    def wlong(self, reg, v, signed, delay=0):
        v &= (1 << 40) - 1
        self.w(reg, v & M32, delay); self.w(reg + 1, (v >> 32) & 0xff, delay)
    def mem(self, fn): self.pend.append((self.cycle + 2, None, fn))
    def br(self, t):
        if any(b[0] == self.cycle + 6 for b in self.branches): raise RuntimeError('two branches in one packet')
        self.branches.append((self.cycle + 6, t & M32))
    def callp(self, t, r):
        self.br(t); self.w(r, self._next_pc); self._callp_idle = 5
    def dint(self): self.gie_saved = self.gie; self.gie = 0
    def rint(self): self.gie = getattr(self, 'gie_saved', 1)
    def mvc_to(self, cr, v):
        if cr == 10: self.tsc_base = self.cycle; return
        if cr == 1: self.gie = v & 1; self.pgie = (v >> 1) & 1; self.cr1 = v & ~3; return
        if cr == 2: self.ifr |= v & ~3; return                 # ISR: set flags
        if cr == 3: self.ifr &= ~v; return                     # ICR: clear flags
        if cr == 4: self.ier = v | 1; return                   # IER (bit 0, NMI/reset, reads as 1)
        setattr(self, f'cr{cr}', v)
    def mvc_from(self, cr):
        if cr == 10: return ((self.cycle - getattr(self, 'tsc_base', 0)) * self.tscl_scale) & M32
        if cr == 1: return getattr(self, 'cr1', 0x0100) & ~3 | self.gie | self.pgie << 1
        if cr == 2: return self.ifr
        if cr == 4: return self.ier
        return getattr(self, f'cr{cr}', 0)
    def raise_int(self, n): self.ifr |= 1 << n
    def b_irp(self, which):
        if which == 6:                                         # return from interrupt: GIE from ITSR (= PGIE)
            self.br(getattr(self, 'cr6', 0)); self.gie = getattr(self, 'cr27', self.pgie) & 1; self.pgie = self.gie
        else: self.br(getattr(self, 'cr7', 0))
    def _take_int(self, pc):
        """At a packet boundary with no branch in flight: highest-priority enabled pending interrupt (4..15).
        In-flight results land first (the pipeline drains), then IRP = pc, PGIE/ITSR = GIE, GIE = 0, vector."""
        p = self.ifr & self.ier & 0xFFF0
        n = (p & -p).bit_length() - 1
        while self.pend: self.tick()
        self.ifr &= ~(1 << n)
        self.cr6 = pc; self.pgie = self.gie; self.cr27 = self.gie; self.gie = 0
        self.ints_taken += 1
        return (getattr(self, 'cr5', 0) & ~0x3FF) + 32 * n

    def _retire(self):
        """apply everything due at the start of self.cycle (memory ops first, then register writes)"""
        if not self.pend: return
        now = [p for p in self.pend if p[0] <= self.cycle]
        if not now: return
        self.pend = [p for p in self.pend if p[0] > self.cycle]
        for c, r, v in now:
            if r is None: v()
        regs = [p for p in self.pend if p[0] <= self.cycle]   # loads issued by the memory ops may already be due
        for c, r, v in now + regs:
            if r is not None and r != 'late': self.R[r] = v
        if regs: self.pend = [p for p in self.pend if p[0] > self.cycle]
        for c, r, v in now:
            if r == 'late': v(self)

    def fetch(self, a):
        I = self.code.get(a)
        if I is None:
            I = self.code[a] = decode(self.rd32(a), a)
        return I

    def packet(self, pc):
        out = []
        while True:
            I = self.fetch(pc); out.append(I); pc += 4
            if not I.p: return out, pc
            if len(out) > 8: raise RuntimeError(f'execute packet too long at {pc:#x}')

    def tick(self):
        self.cycle += 1
        self._retire()

    def run(self, pc, stop, max_cycles=200_000_000):
        """run from pc until control reaches `stop` (a sentinel return address)"""
        idle = 0
        while True:
            if self.branches and self.branches[0][0] <= self.cycle:
                pc = self.branches.pop(0)[1]; idle = 0
            if idle:
                idle -= 1; self.tick(); continue
            if pc == stop:
                while self.pend: self.tick()
                if self.branches: raise RuntimeError(f'returned with branches in flight: {self.branches}')
                return
            if pc in self.mocks:
                self._mock(pc); pc = self.R[ridx(1, 3)]; continue
            if self.cycle > max_cycles: raise RuntimeError('cycle limit')
            if self.next_event is not None and self.cycle >= self.next_event: self.next_event = self.on_event(self)
            if self.gie and self.ifr & self.ier & 0xFFF0 and self.ier & 2 and not self.branches:
                pc = self._take_int(pc); continue
            pk, nxt = self.packet(pc)
            self._next_pc = nxt; self._callp_idle = 0
            R = list(self.R)
            for I in pk:
                if I.pred is not None and (R[I.pred[0]] != 0) == bool(I.pred[1]): continue
                I.ex(self, R)
            self.ninstr += len(pk)
            if self.trace is not None: self.trace(self, pc, pk)
            idle = max(max(I.nops for I in pk), self._callp_idle)
            pc = nxt
            self.tick()

    def _mock(self, pc):
        while self.pend:                  # let everything in flight land before the "call"
            self.tick()
        self.calls.append(pc)
        self.mocks[pc](self)
        a4 = self.R[4]
        for r in self.CALLER_SAVED:
            if r not in (4, 35): self.R[r] = self.mock_clobber   # keep A4 (result) and B3 (return)
        self.R[4] = a4
        for _ in range(6): self.tick()

    def arg(self, i):
        return self.R[(4, 36, 6, 38, 8, 40, 10, 42, 12, 44)[i]]

    def call(self, entry, args=(), sp=0xc0f00000, ret=0xcafe0000, max_cycles=500_000_000):
        regs = (4, 36, 6, 38, 8, 40, 10, 42, 12, 44)
        for i in range(64): self.R[i] = 0xA5A50000 + i      # junk, so uninitialised use shows up
        for r, v in zip(regs, args): self.R[r] = v & M32
        self.R[ridx(1, 15)] = sp; self.R[ridx(1, 3)] = ret; self.R[ridx(1, 14)] = 0xc0812a00   # firmware DP
        saved = {r: self.R[r] for r in list(range(10, 16)) + list(range(32 + 10, 32 + 16))}
        self.pend = []; self.branches = []
        self.run(entry, ret, max_cycles)
        bad = [rname(r) for r, v in saved.items() if self.R[r] != v]
        if bad: raise RuntimeError(f'callee-saved registers not restored: {bad}')
        return self.R[4]

def load_elf(sim, path):
    import celf
    base, img, syms, top = celf.image(path)
    sim.write(base, img)
    return base, img, syms, top
