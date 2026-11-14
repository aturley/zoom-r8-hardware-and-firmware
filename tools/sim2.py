"""Byte-addressed cycle simulator for code emitted by asm6x (serial, plain fetch packets).
Decodes directly from instruction fields; supports exactly the forms asm6x emits:
MVK/MVKH (pred), ADDK, CALLP, B disp (pred), B .S2 B3 (raw), NOP n, LD/ST (+off, post++; W/BU/B),
L-unit ADD / ADD scst5 / SUB / CMPLTU, MVC to/from TSCL (pred).
Load: 4 delay slots. Branch: 5 delay slots. Calls go to Python mocks."""
import struct

PREG = {1: 'B0', 2: 'B1', 3: 'B2', 4: 'A1', 5: 'A2', 6: 'A0'}


def sx(v, n):
    return v - (1 << n) if v & (1 << (n - 1)) else v


def R(side, n):
    return ('A' if side == 0 else 'B') + str(n)


class Sim:
    def __init__(self, blob, base, mocks, sp=0xc0900000):
        self.m = bytearray()
        self.mem = {}
        for i, b in enumerate(blob):
            self.mem[base + i] = b
        self.base, self.end = base, base + len(blob)
        self.ranges = [(base, base + len(blob))]
        self.R = {f'{s}{n}': 0 for s in 'AB' for n in range(32)}
        self.R['B15'] = sp
        self.R['B3'] = 0xdeadbee0
        self.mocks = mocks
        self.pending, self.branch, self.cycle = [], None, 0
        self.tscl = None          # None = stopped
        self.clobber = 0xBADBAD00
        self.trace = []

    def add_region(self, base, data):
        for i, b in enumerate(data): self.mem[base + i] = b
        self.ranges.append((base, base + len(data)))

    def rd8(self, a): return self.mem.get(a & 0xffffffff, 0)
    def wr8(self, a, v): self.mem[a & 0xffffffff] = v & 0xff
    def rd32(self, a): return sum(self.rd8(a + i) << (8 * i) for i in range(4))
    def wr32(self, a, v):
        for i in range(4): self.wr8(a + i, (v >> (8 * i)) & 0xff)
    def rdbytes(self, a, n): return bytes(self.rd8(a + i) for i in range(n))

    def tick(self):
        self.cycle += 1
        if self.tscl is not None: self.tscl += 1
        for p in [p for p in self.pending if p[0] <= self.cycle]:
            self.R[p[1]] = p[2]; self.pending.remove(p)

    def predok(self, w):
        c, z = (w >> 29) & 7, (w >> 28) & 1
        if c == 0: return True
        return (self.R[PREG[c]] != 0) ^ bool(z)

    def run(self, pc, max_cycles=5_000_000):
        while True:
            if self.cycle > max_cycles: raise RuntimeError('cycle limit')
            if not any(lo <= pc < hi for lo, hi in self.ranges):
                if pc in self.mocks and getattr(self, 'far_mocks', False):   # indirect call to a firmware function
                    self.trace.append(('farcall', pc))
                    ret = self.R['B3']
                    self.mocks[pc](self)
                    for r in ('A0', 'A1', 'A2', 'A3', 'A5', 'A6', 'A7', 'A8', 'A9', 'B0', 'B1', 'B2', 'B4', 'B5', 'B6', 'B7', 'B8', 'B9'):
                        self.R[r] = self.clobber
                    pc = ret
                    continue
                return pc
            w = struct.unpack('<I', bytes(self.rd8(pc + i) for i in range(4)))[0]
            nxt = pc + 4
            s = (w >> 1) & 1
            op5 = (w >> 2) & 0x1f
            if (w & 0x1ffc) == 0x0120:                                # BNOP disp,N (normal packet: x4)
                c12 = sx((w >> 16) & 0xfff, 12)
                if self.predok(w):
                    self.branch = (self.cycle + 6, ((pc & ~31) + (c12 << 2)) & 0xffffffff)
                self.tick()
                for _ in range((w >> 13) & 7):
                    if self.branch and self.branch[0] <= self.cycle: break
                    self.tick()
            elif w & 0xfffe1ffe == 0:                                   # NOP n (or 0)
                n = ((w >> 13) & 0xf) + 1 if w else 1
                for _ in range(n):
                    self.tick()
                    if self.branch and self.branch[0] <= self.cycle: break
            else:
                ok = self.predok(w)
                if (w & ~(0x1f << 18)) == 0x0080a362:                # B .S2 Bn (indirect)
                    if ok: self.branch = (self.cycle + 6, self.R['B%d' % ((w >> 18) & 0x1f)])
                elif (w >> 2) & 0xf == 0b1010:                        # MVK / MVKH
                    d = R(s, (w >> 23) & 0x1f); c = (w >> 7) & 0xffff
                    if ok:
                        if (w >> 6) & 1: self.R[d] = (c << 16) | (self.R[d] & 0xffff)
                        else: self.R[d] = sx(c, 16) & 0xffffffff
                elif op5 == 0b10100:                                  # ADDK
                    d = R(s, (w >> 23) & 0x1f)
                    if ok: self.R[d] = (self.R[d] + sx((w >> 7) & 0xffff, 16)) & 0xffffffff
                elif op5 == 0b00100:                                  # B / CALLP disp
                    t = ((pc & ~31) + (sx((w >> 7) & 0x1fffff, 21) << 2)) & 0xffffffff
                    if (w >> 28) == 1 and any(lo <= t < hi for lo, hi in self.ranges):   # call into our own code
                        self.R['B3'] = nxt
                        self.trace.append(('icall', t))
                        for _ in range(6): self.tick()
                        pc = t
                        continue
                    if (w >> 28) == 1:
                        assert not self.pending, f'call with loads in flight at {pc:#x}'
                        self.trace.append(('call', t))
                        self.R['B3'] = nxt
                        self.mocks[t](self)
                        for r in ('A0', 'A1', 'A2', 'A3', 'A5', 'A6', 'A7', 'A8', 'A9', 'B0', 'B1', 'B2', 'B4', 'B5', 'B6', 'B7', 'B8', 'B9'):
                            self.R[r] = self.clobber
                        for _ in range(6): self.tick()
                    elif ok:
                        self.branch = (self.cycle + 6, t)
                elif (w >> 2) & 3 == 0b01:                            # LD/ST base+offset
                    op, y, mode = (w >> 4) & 7, (w >> 7) & 1, (w >> 9) & 0xf
                    off, bn, d = (w >> 13) & 0x1f, (w >> 18) & 0x1f, R(s, (w >> 23) & 0x1f)
                    base = R(y, bn)
                    size = {1: 1, 3: 1, 6: 4, 7: 4}[op]
                    if mode == 1:
                        ea = self.R[base] + off * size
                    elif mode == 11:
                        ea = self.R[base]
                        if ok: self.R[base] = (self.R[base] + off * size) & 0xffffffff
                    else:
                        raise RuntimeError(f'mode {mode} at {pc:#x}')
                    if ok:
                        if op in (3, 7):
                            v = self.R[d]
                            (self.wr8(ea, v) if size == 1 else self.wr32(ea, v))
                        else:
                            v = self.rd8(ea) if size == 1 else self.rd32(ea)
                            self.pending.append((self.cycle + 5, d, v))
                elif (w >> 2) & 7 == 0b110:                           # L unit
                    op = (w >> 5) & 0x7f; x = (w >> 12) & 1
                    d = R(s, (w >> 23) & 0x1f); s2 = self.R[R(s ^ x, (w >> 18) & 0x1f)]
                    s1f = (w >> 13) & 0x1f
                    if ok:
                        if op == 0b0000011: v = self.R[R(s, s1f)] + s2
                        elif op == 0b0000010: v = sx(s1f, 5) + s2
                        elif op == 0b0000111: v = self.R[R(s, s1f)] - s2
                        elif op == 0b1011111: v = 1 if self.R[R(s, s1f)] < s2 else 0
                        elif op == 0b1111011: v = self.R[R(s, s1f)] & s2
                        else: raise RuntimeError(f'L op {op:07b} at {pc:#x}')
                        self.R[d] = v & 0xffffffff
                elif (w >> 2) & 0x3ff in (0b0011111000, 0b0011101000):   # MVC
                    frm = ((w >> 6) & 0x3f) == 0b001111
                    self.creg = getattr(self, 'creg', {'CSR': 0x0100_0003, 'IER': 0xFFFF, 'ICR': 0})
                    CN = {0b00001: 'CSR', 0b00100: 'IER', 0b00011: 'ICR'}
                    if frm:
                        cr = (w >> 18) & 0x1f
                        if ok:
                            if cr == 0b01010:
                                self.latched = (self.tscl or 0) + getattr(self, 'tsc_base', 0)
                                self.R[R(1, (w >> 23) & 0x1f)] = self.latched & 0xffffffff
                            elif cr == 0b01011:
                                self.R[R(1, (w >> 23) & 0x1f)] = (getattr(self, 'latched', 0) >> 32) & 0xffffffff
                            else:
                                self.R[R(1, (w >> 23) & 0x1f)] = self.creg[CN[cr]]
                    else:
                        cr = (w >> 23) & 0x1f
                        if ok:
                            if cr == 0b01010:
                                self.trace.append(('tscl_start', self.cycle))
                                if self.tscl is None: self.tscl = 0
                            else:
                                self.creg[CN[cr]] = self.R[R(1, (w >> 18) & 0x1f)]
                                self.trace.append(('mvc', CN[cr], self.creg[CN[cr]]))
                else:
                    raise RuntimeError(f'unsupported word {w:08x} at {pc:#x}')
                self.tick()
            if self.branch and self.branch[0] <= self.cycle:
                pc = self.branch[1]; self.branch = None
            else:
                pc = nxt
