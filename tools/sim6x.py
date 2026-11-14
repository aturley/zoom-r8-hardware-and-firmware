"""Cycle-level simulator for the serial subset emitted by asm6x (one instruction per execute packet).
Models: LDW 4 delay slots, B 5 delay slots, CALLP (call + return, no visible delay), NOP n, MVKL/MVKH/ADDK,
predicated branches. External functions are Python mocks keyed by address."""
import struct
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import c6x


class Sim:
    def __init__(self, blob, base, mocks, sp=0xc0900000):
        self.mem = {}
        for o in range(0, len(blob), 4):
            self.mem[base + o] = struct.unpack_from('<I', blob, o)[0]
        self.base, self.end = base, base + len(blob)
        self.R = {f'{s}{n}': 0 for s in 'AB' for n in range(32)}
        self.R['B15'] = sp
        self.R['B3'] = 0xdeadbee0          # caller's return address
        self.mocks = mocks
        self.pending = []                  # (cycle_visible, reg, value)
        self.branch = None                 # (cycle_taken, target)
        self.cycle = 0
        self.trace = []
        self.clobber = 0xBADBAD00

    def rd(self, a):
        return self.mem.get(a, 0)

    def wr(self, a, v):
        self.mem[a] = v & 0xffffffff

    def tick(self, n=1):
        for _ in range(n):
            self.cycle += 1
            for p in [p for p in self.pending if p[0] <= self.cycle]:
                self.R[p[1]] = p[2]
                self.pending.remove(p)

    def run(self, pc, max_cycles=200000):
        while True:
            if self.cycle > max_cycles:
                raise RuntimeError('cycle limit')
            if not (self.base <= pc < self.end):
                return pc                   # left the routine (tail call / return)
            w = self.rd(pc)
            i = c6x.dec32(w, pc, pc & ~31)
            nxt = pc + 4
            k = i.kind
            if k == 'nop':
                n = ((w >> 13) & 0xf) + 1 if w else 1
                for _ in range(n):
                    self.tick()
                    if self.branch and self.branch[0] <= self.cycle:
                        break
            else:
                if k == 'mvkl':
                    self.R[c6x.reg(*i.dst)] = c6x.sx(i.imm, 16) & 0xffffffff
                elif k == 'mvkh':
                    r = c6x.reg(*i.dst); self.R[r] = (i.imm << 16) | (self.R[r] & 0xffff)
                elif k == 'addk':
                    r = c6x.reg((w >> 1) & 1, (w >> 23) & 0x1f)
                    self.R[r] = (self.R[r] + c6x.sx((w >> 7) & 0xffff, 16)) & 0xffffffff
                elif k in ('ld', 'st'):
                    y, bn, mode, off = i.imm
                    ea = (self.R[c6x.reg(y, bn)] + off) & 0xffffffff
                    r = c6x.reg(*i.dst)
                    if k == 'st':
                        self.wr(ea, self.R[r])
                    else:
                        self.pending.append((self.cycle + 5, r, self.rd(ea)))
                elif k == 'call':
                    self.trace.append(('call', i.target))
                    assert not self.pending, 'call with loads in flight'
                    self.R['B3'] = nxt
                    self.mocks[i.target](self)
                    for r in ('A1', 'A2', 'A3', 'A5', 'A6', 'A7', 'A8', 'A9', 'B0', 'B1', 'B2', 'B4', 'B5', 'B6', 'B7', 'B8', 'B9'):
                        self.R[r] = self.clobber  # clobber caller-saved registers
                    self.tick(6)
                elif k == 'b':
                    c = (w >> 29) & 7; z = (w >> 28) & 1
                    take = True
                    if c:
                        rn = {1: 'B0', 2: 'B1', 3: 'B2', 4: 'A1', 5: 'A2', 6: 'A0'}[c]
                        take = (self.R[rn] != 0) ^ z
                    if take:
                        self.branch = (self.cycle + 6, i.target)
                else:
                    raise RuntimeError(f'unsupported at {pc:#x}: {i.text}')
                self.tick()
            if self.branch and self.branch[0] <= self.cycle:
                pc = self.branch[1]; self.branch = None
            else:
                pc = nxt
