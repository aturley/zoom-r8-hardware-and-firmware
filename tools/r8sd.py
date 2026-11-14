"""MMCSD1 controller + SD card model for sim674. Controller: MMCST0 latched bits clear on read, DRRDY tracks the
receive FIFO (>= 32 bytes at FIFOLEV 1), DATDNE is set once the card has sent the whole block (possibly before the
CPU has drained the FIFO), 48-bit responses in MMCRSP67, 136-bit in RSP67..RSP01. Card: SD 2.0 state machine
(idle, ready, ident, stby, tran), SDHC (CCS, block addressing) or SDSC, CMD55/ACMD, bus width, CSD 2.0/1.0.
Rule checks go to .errors: identification clock <= 400 kHz, transfer clock <= 25 MHz, bus widths matching."""
SD = 0x01E1B000
MODCLK = 180_000_000

class SdModel:
    def __init__(self, sim, blocks=65536, hc=True, image=None, present=True):
        self.sim, self.blocks, self.hc, self.present = sim, blocks, hc, present
        self.image = image or {}                        # block -> 512 bytes (missing: pattern)
        self.r = {}; self.st = 0; self.fifo = bytearray(); self.pending = bytearray()
        self.state = 'idle'; self.app = False; self.acmd41 = 0; self.rca = 0x1234; self.width = 1
        self.blen = 512; self.errors = []; self.log = []; self.reads = 0; self.read_faults = 0
        self.wfifo = bytearray(); self.w_left = 0; self.w_buf = bytearray(); self.w_lba = 0; self.multi = False
        self.w_count = 0; self.w_carry = bytearray(); self.w_dmatrig = False
        self.prg_polls = 0; self.writes = []; self.fail_write_at = None; self.blocks_written = 0
        sim.io[SD >> 12] = self.io
    def block(self, n):
        if n in self.image: return self.image[n]
        return bytes(((n * 7 + i * 13) ^ (n >> 8)) & 0xFF for i in range(512))
    def clock_hz(self):
        c = self.r.get(0x04, 0)
        return MODCLK / (2 * ((c & 0xFF) + 1)) if c & 0x100 else 0
    def io(self, sim, a, size, v):
        o = a - SD
        if v is None:
            if o == 0x08:
                self.feed(); self.wfeed()
                s = self.st; self.st = 0; return s       # DRRDY is latched too (hardware: fires once per level crossing)
            if o == 0x0C:
                self.feed(); self.wfeed()
                f = self.wfifo if self.r.get(0x74, 0) & 2 else self.fifo
                return (1 << 5 if not f else 0) | (1 << 6 if len(f) >= 64 else 0) | (1 << 4)
            if o == 0x28:
                b = bytes(self.fifo[:4]); del self.fifo[:4]; self.feed()
                return int.from_bytes(b.ljust(4, b'\0'), 'little')
            if 0x38 <= o <= 0x44: return self.rsp[(o - 0x38) // 4]
            return self.r.get(o, 0)
        self.r[o] = v
        if o == 0x74 and v & 1: self.fifo.clear(); self.pending.clear(); self.wfifo.clear()
        if o == 0x2C:
            if not self.r.get(0x74, 0) & 2: self.errors.append('MMCDXR written with the FIFO in read direction')
            self.wfifo += (v & 0xFFFFFFFF).to_bytes(4, 'little')
            if len(self.wfifo) > 64: self.errors.append('write FIFO overflow (written while FIFOFUL)'); del self.wfifo[64:]
        if o == 0x30: self.command(v)
    rsp = [0, 0, 0, 0]
    def feed(self):                                    # card -> FIFO, 32 bytes at a time
        while self.pending and len(self.fifo) < 64:
            before = len(self.fifo)
            self.fifo += self.pending[:32]; del self.pending[:32]
            if before < 32 <= len(self.fifo): self.st |= 1 << 10   # DRRDY latches on crossing the level
            if not self.pending: self.st |= 1          # DATDNE: the whole block has arrived
    def wfeed(self):                                   # FIFO -> card, at most 32 bytes per status poll
        if self.state != 'rcv' or not self.w_left: return
        take = min(32, len(self.wfifo))
        data = self.wfifo[:take]; del self.wfifo[:take]
        for b in data:
            if self.w_count == 4095 and not self.w_dmatrig: self.w_carry.append(0); self.w_count += 1   # the R8 controller's fault without DMATRIG (measured)
            self.w_carry.append(b); self.w_count += 1
        while self.w_carry and self.w_left:
            k = min(512 - len(self.w_buf), len(self.w_carry))
            self.w_buf += self.w_carry[:k]; del self.w_carry[:k]
            if len(self.w_buf) == 512: self.commit_block()
        if not self.w_left: self.w_carry.clear()
    def commit_block(self):
        if True:
            if self.fail_write_at == self.w_lba: self.st |= 1 << 5     # CRC status error: block not programmed
            else: self.image[self.w_lba] = bytes(self.w_buf); self.blocks_written += 1
            self.w_buf = bytearray(); self.w_lba += 1; self.w_left -= 1
            if not self.w_left:
                self.st |= 1                           # DATDNE
                if not self.multi: self.state = 'prg'; self.prg_polls = 3
    def resp48(self, bits39_8): self.rsp = [0, 0, 0, bits39_8 & 0xFFFFFFFF]
    def command(self, v):
        c, fmt, arg = v & 0x3F, (v >> 9) & 3, self.r.get(0x34, 0)
        app, self.app = self.app, False
        hz = self.clock_hz()
        self.log.append((('A' if app else '') + 'CMD%d' % c, arg))
        if not hz: self.errors.append(f'CMD{c} with the clock off')
        if self.state in ('idle', 'ready', 'ident') and hz > 400_000: self.errors.append(f'CMD{c} during identification at {hz/1e3:.0f} kHz')
        if hz > 25_000_000: self.errors.append(f'CMD{c} at {hz/1e6:.1f} MHz')
        if not self.present: self.st |= 1 << 4; return
        status = {'idle': 0, 'ready': 1, 'ident': 2, 'stby': 3, 'tran': 4, 'data': 5, 'rcv': 6, 'prg': 7}[self.state] << 9 | (1 << 5 if app else 0)
        if self.state == 'tran': status |= 1 << 8      # READY_FOR_DATA
        if self.state == 'rcv' and c not in (12, 13): self.errors.append(f'CMD{c} while receiving write data')
        ok = lambda: setattr(self, 'st', self.st | 4 | (2 if v & (1 << 8) else 0))
        if c == 0: self.state = 'idle'; self.width = 1; self.acmd41 = 0; ok(); return
        if c == 8 and self.state == 'idle': self.resp48((arg & 0xFFF) << 0); ok(); return
        if c == 55: self.app = True; self.resp48(status | 1 << 5); ok(); return
        if app and c == 41:
            if fmt != 3: self.errors.append('ACMD41 not as R3')
            self.acmd41 += 1
            ready = self.acmd41 >= 3
            ocr = 0x00FF8000 | (0x80000000 if ready else 0) | (0x40000000 if ready and self.hc and arg & 0x40000000 else 0)
            if ready: self.state = 'ready'
            self.rsp = [0, 0, 0, ocr]; ok(); return
        if c == 2 and self.state == 'ready':
            self.state = 'ident'
            self.rsp = [0x01504E41 & 0, 0, 0, 0]
            cid = (0x03 << 120) | (0x5344 << 104) | (int.from_bytes(b'SU32G', 'big') << 64) | (0x80 << 56) | (0x12345678 << 24) | (0x0123 << 8) | 1
            self.rsp = [(cid >> (32 * i)) & 0xFFFFFFFF for i in range(4)]; ok(); return
        if c == 3 and self.state in ('ident', 'stby'): self.state = 'stby'; self.resp48(self.rca << 16 | 0x0500); ok(); return
        if c == 9 and self.state == 'stby' and arg >> 16 == self.rca:
            if self.hc: csd = (1 << 126) | ((self.blocks // 1024 - 1) << 48) | (0x0E << 112)
            else: csd = (9 << 80) | (((self.blocks * 512 >> 9 >> (7 + 2)) - 1) << 62) | (7 << 47)
            self.rsp = [(csd >> (32 * i)) & 0xFFFFFFFF for i in range(4)]; ok(); return
        if c == 7:
            if arg >> 16 == self.rca and self.state == 'stby':
                if not v & (1 << 8): self.errors.append('CMD7 without busy wait')
                self.state = 'tran'; self.resp48(status); ok(); return
        if app and c == 6 and self.state == 'tran': self.width = 4 if arg == 2 else 1; self.resp48(status); ok(); return
        if c == 16 and self.state == 'tran': self.blen = arg; self.resp48(status); ok(); return
        if c == 13:
            self.resp48(status); ok()
            if self.state == 'prg':
                self.prg_polls -= 1
                if self.prg_polls <= 0: self.state = 'tran'
            return
        if c == 12 and self.state == 'data':                 # end of a multi-block read
            self.pending.clear(); self.resp48(status); ok(); self.state = 'tran'; return
        if c == 12:
            if self.state != 'rcv': self.errors.append(f'CMD12 in state {self.state}')
            if self.w_buf: self.errors.append('CMD12 in the middle of a block')
            if self.w_left and self.multi: self.errors.append(f'CMD12 with {self.w_left} blocks not sent')
            self.resp48(status); ok(); self.state = 'prg'; self.prg_polls = 3; self.w_left = 0; return
        if c in (24, 25) and self.state == 'tran':
            n = arg if self.hc else arg // 512
            nb = self.r.get(0x20)
            if not self.hc and arg % 512: self.errors.append('SDSC write address not block aligned')
            if (v & (1 << 13 | 1 << 11)) != (1 << 13 | 1 << 11): self.errors.append(f'CMD{c} without WDATX + DTRW')
            if self.r.get(0x1C) != 512 or (c == 24 and nb != 1) or (c == 25 and not nb): self.errors.append(f'CMD{c} with BLEN/NBLK {self.r.get(0x1C)}/{nb}')
            if not self.r.get(0x74, 0) & 2: self.errors.append(f'CMD{c} with the FIFO in read direction')
            if v & (1 << 15): self.wfifo.clear()      # DCLR empties the FIFO (seen on the R8): a pre-fill is lost
            ctl_w = 4 if self.r.get(0x00, 0) & 4 else 1
            if ctl_w != self.width: self.errors.append(f'bus width card {self.width} controller {ctl_w}')
            if n + (nb or 1) > self.blocks: self.errors.append(f'write past the card: {n}+{nb}')
            self.resp48(status); ok()
            self.state = 'rcv'; self.multi = c == 25; self.w_lba = n; self.w_left = nb or 1; self.w_buf = bytearray()
            self.w_count = 0; self.w_carry = bytearray(); self.w_dmatrig = bool(v & (1 << 16))
            self.writes.append((n, nb)); self.wfeed(); return
        if c == 17 and self.state == 'tran':
            n = arg if self.hc else arg // 512
            if not self.hc and arg % 512: self.errors.append('SDSC read address not block aligned')
            if not v & (1 << 13): self.errors.append('CMD17 without WDATX (bit 13)')
            if v & (1 << 11): self.errors.append('CMD17 with DTRW (write direction)')
            if (self.r.get(0x1C), self.r.get(0x20)) != (512, 1): self.errors.append('CMD17 with MMCBLEN/MMCNBLK not 512/1')
            ctl_w = 4 if self.r.get(0x00, 0) & 4 else 1
            self.resp48(status); ok()
            if ctl_w != self.width: self.st |= 1 << 6; self.errors.append(f'bus width card {self.width} controller {ctl_w}'); return
            if n >= self.blocks: self.st |= 1 << 3; return
            self.pending = bytearray(self.block(n)); self.reads += 1; self.feed(); return
        if c == 18 and self.state == 'tran':
            n = arg if self.hc else arg // 512; nb = self.r.get(0x20, 0)
            if not v & (1 << 13): self.errors.append('CMD18 without WDATX (bit 13)')
            if self.r.get(0x1C) != 512 or nb < 2: self.errors.append(f'CMD18 with MMCBLEN/MMCNBLK {self.r.get(0x1C)}/{nb}')
            if n + nb > self.blocks: self.st |= 1 << 3; return
            self.resp48(status); ok(); self.state = 'data'
            data = bytearray(b''.join(self.block(n + k) for k in range(nb)))
            if len(data) >= 4096 and not v & (1 << 16): del data[4094]; self.read_faults += 1   # without DMATRIG the R8 drops this byte (measured 2026-11-14: already at exactly 4 KB)
            self.pending = data; self.reads += nb; self.feed(); return
        self.st |= 1 << 4                              # anything else: no response
        self.errors.append(f'unexpected {"A" if app else ""}CMD{c} in state {self.state}')
