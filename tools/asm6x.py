"""Tiny C674x assembler for serial (non-parallel) 32-bit code in plain (header-less) fetch packets.

Only instruction forms whose encodings were confirmed by decoding the R8 firmware are supported:
MVKL/MVKH (.S), ADDK (.S), CALLP (.S2, return in B3), B disp (.S, optional predicate),
LDW/STW base+offset (*+R[ucst5], word-scaled), NOP n.
Every instruction is its own execute packet (p-bit 0); delay slots must be filled with NOP explicitly.
"""
import struct

PRED = {None: (0, 0), 'B0': (1, 0), '!B0': (1, 1), 'B1': (2, 0), '!B1': (2, 1), 'B2': (3, 0), '!B2': (3, 1),
        'A1': (4, 0), '!A1': (4, 1), 'A2': (5, 0), '!A2': (5, 1), 'A0': (6, 0), '!A0': (6, 1)}


def reg(r):
    side = 0 if r[0] == 'A' else 1
    n = int(r[1:])
    assert 0 <= n < 32
    return side, n


def pred(p):
    c, z = PRED[p]
    return (c << 29) | (z << 28)


class Asm:
    def __init__(self, base):
        assert base % 32 == 0
        self.base = base
        self.items = []      # (kind, args)
        self.labels = {}

    def label(self, name):
        self.labels[name] = self.base + 4 * len(self.items)

    def _emit(self, *it):
        self.items.append(it)

    # ---- instructions ----
    def mvkl(self, v, r): self._emit('mvk', v & 0xffff, r, 0)
    def mvkh(self, v, r): self._emit('mvk', (v >> 16) & 0xffff, r, 1)
    def mvk32(self, v, r): self.mvkl(v, r); self.mvkh(v, r)
    def addk(self, v, r): self._emit('addk', v, r)
    def callp(self, target): self._emit('callp', target)
    def b(self, target, p=None): self._emit('b', target, p)
    def bnop(self, target, p=None, n=5): self._emit('bnop', target, p, n)
    def ldw(self, base, off, dst): self._emit('ldst', 'ldw', base, off, dst)
    def stw(self, src, base, off): self._emit('ldst', 'stw', base, off, src)
    def nop(self, n=1): self._emit('nop', n)

    def word(self, n, it):
        addr = self.base + 4 * n
        pce1 = addr & ~31
        k = it[0]
        if k == 'mvk':
            _, cst, r, h = it
            s, d = reg(r)
            return (d << 23) | (cst << 7) | (h << 6) | (0b1010 << 2) | (s << 1)
        if k == 'addk':
            _, v, r = it
            s, d = reg(r)
            assert -32768 <= v < 32768
            return (d << 23) | ((v & 0xffff) << 7) | (0b10100 << 2) | (s << 1)
        if k == 'bnop':
            t = self.labels.get(it[1], it[1])
            disp = t - pce1
            assert disp % 4 == 0
            cst = disp // 4
            assert -(1 << 11) <= cst < (1 << 11), hex(t)
            return pred(it[2]) | ((cst & 0xfff) << 16) | (it[3] << 13) | 0x0120 | (1 << 1)
        if k in ('callp', 'b'):
            t = it[1]
            t = self.labels.get(t, t)
            disp = (t - pce1)
            assert disp % 4 == 0
            cst = disp // 4
            assert -(1 << 20) <= cst < (1 << 20), hex(t)
            if k == 'callp':
                return (1 << 28) | ((cst & 0x1fffff) << 7) | (0b00100 << 2) | (1 << 1)
            return pred(it[2]) | ((cst & 0x1fffff) << 7) | (0b00100 << 2) | (1 << 1)
        if k == 'ldst':
            _, op, base, off, r = it
            ys, bn = reg(base)
            s, d = reg(r)
            assert off % 4 == 0 and 0 <= off // 4 < 32
            opc = 6 if op == 'ldw' else 7
            mode = 1  # *+R[ucst5]
            return (d << 23) | (bn << 18) | ((off // 4) << 13) | (mode << 9) | (0 << 8) | (ys << 7) | (opc << 4) | (0b01 << 2) | (s << 1)
        if k == 'nop':
            n_ = it[1]
            assert 1 <= n_ <= 9
            return (n_ - 1) << 13
        raise ValueError(k)

    def assemble(self):
        out = []
        for n, it in enumerate(self.items):
            out.append(self.word(n, it))
        while len(out) % 8:
            out.append(0)       # NOP padding to a full fetch packet
        for k in range(7, len(out), 8):
            assert out[k] >> 28 != 0xE, 'word 7 of a packet would look like a compact header'
        return b''.join(struct.pack('<I', w) for w in out)


# ---- extensions (encodings cross-checked against firmware words; see logger build) ----
L_OPS = {'add': 0b0000011, 'add_cst': 0b0000010, 'cmpltu': 0b1011111, 'sub': 0b0000111, 'and': 0b1111011}
LDST_OPC = {'ldbu': (1, 0), 'stb': (3, 0), 'ldw': (6, 0), 'stw': (7, 0), 'sth': (5, 0)}
MODES = {'+off': 1, 'post++': 11}
CREG = {'TSCL': 0b01010, 'TSCH': 0b01011, 'CSR': 0b00001, 'IER': 0b00100, 'ICR': 0b00011}


def _l(self, op, src1, src2, dst, cst=None):
    self._emit('l', op, src1, src2, dst, cst)


def _mvc_from(self, creg, dst): self._emit('mvcf', creg, dst)
def _mvc_to(self, src, creg, p=None): self._emit('mvct', src, creg, p)
def _ldst2(self, op, base, off, r, mode='+off'): self._emit('ldst2', op, base, off, r, mode)
def _raw(self, w): self._emit('raw', w)
def _mvk_p(self, v, r, p): self._emit('mvkp', v & 0xffff, r, p)


Asm.l = _l
Asm.mvc_from = _mvc_from
Asm.mvc_to = _mvc_to
Asm.ldst2 = _ldst2
Asm.raw = _raw
Asm.mvk_p = _mvk_p
_old_word = Asm.word


def _word(self, n, it):
    k = it[0]
    if k == 'l':
        _, op, src1, src2, dst, cst = it
        s, d = reg(dst)
        s2side, s2 = reg(src2)
        x = 1 if s2side != s else 0
        if op == 'add_cst':
            c = src1 & 0x1f
            return (d << 23) | (s2 << 18) | (c << 13) | (x << 12) | (L_OPS[op] << 5) | (0b110 << 2) | (s << 1)
        s1side, s1 = reg(src1)
        assert s1side == s, 'src1 must be on the unit side'
        return (d << 23) | (s2 << 18) | (s1 << 13) | (x << 12) | (L_OPS[op] << 5) | (0b110 << 2) | (s << 1)
    if k == 'mvcf':
        _, creg, dst = it
        s, d = reg(dst); assert s == 1, 'MVC uses .S2 / B registers'
        return (d << 23) | (CREG[creg] << 18) | (0 << 13) | (0b001111 << 6) | (0b1000 << 2) | (1 << 1)
    if k == 'mvct':
        _, src, creg, p = it
        s, r = reg(src); assert s == 1
        return pred(p) | (CREG[creg] << 23) | (r << 18) | (0 << 13) | (0b001110 << 6) | (0b1000 << 2) | (1 << 1)
    if k == 'ldst2':
        _, op, base, off, r, mode = it
        ys, bn = reg(base); s, d = reg(r)
        opc, rbit = LDST_OPC[op]
        return (d << 23) | (bn << 18) | ((off & 0x1f) << 13) | (MODES[mode] << 9) | (rbit << 8) | (ys << 7) | (opc << 4) | (0b01 << 2) | (s << 1)
    if k == 'raw':
        return it[1]
    if k == 'mvkp':
        _, cst, r, p = it
        s, d = reg(r)
        return pred(p) | (d << 23) | (cst << 7) | (0b1010 << 2) | (s << 1)
    return _old_word(self, n, it)


Asm.word = _word
