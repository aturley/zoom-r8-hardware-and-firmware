"""Peripheral models for running a standalone R8 firmware in sim674: PSC, SYSCFG, GPIO, SPI1 + ST7565 LCD,
UART2 + panel, McASP0 transmit (slot clock from the CPU cycle count). Register-level, only what our code uses."""
import struct, zlib

class Board:
    def __init__(self, sim, fs=44100, cpu_hz=360_000_000, slots=2, clock=True, time_scale=1):
        """time_scale k: the firmware's TSCL runs k times fast and the audio clock/panel timing shrink to match,
        so k simulated seconds take one second of cycles (the CPU budget per audio slot shrinks k-fold too)."""
        self.sim, self.fs, self.slots, self.clock, self.k = sim, fs, slots, clock, time_scale
        self.cpu_hz = cpu_hz / time_scale; sim.tscl_scale = time_scale
        self.gbl = 0; self.xbuf4 = []; self.late_at = []
        self.gpio = {}                     # register offset -> value (OUT data per bank pair)
        self.ext = {0xB0: 1 << 12,         # input pin levels per bank pair: power switch GP8[12] on
                    0x10: 1 << 7 | 1 << 20 | 1 << 21}   # board ID straps GP0[7], GP1[4], GP1[5] high: board 7 (the user's R8)
        for o, d, out in ((0x10, 0x07BCFC80, 0x00400000), (0x38, 0x2400, 0x4000), (0x60, 0, 0),
                          (0x88, 0x001E4C3F, 0xE1C10000), (0xB0, 0xFFFFFCE1, 0x300)):
            self.gpio[o] = d; self.gpio[o + 4] = out     # as the stock firmware leaves them (PERIPH.BIN)
        self.lcd = bytearray(1024); self.page = 0; self.col = 0; self.lcd_cmds = []
        self.rx = []                       # (cycle, byte) the panel sends
        self.tx = []                       # bytes the firmware sends to the panel
        self.panel_pos = [0] * 9           # physical fader positions (1-8, MASTER), reported on a snapshot request
        self.samples = []                  # (cycle, slot value) written to McASP XBUF
        self.mcasp_start = None            # cycle of the first XBUF write: slot clock starts there
        self.late = 0; self.mcasp_regs = {}
        self.rx_start = None; self.rx_reads = []; self.rx_overruns = 0   # McASP receive (serializer 1)
        self.rx_last = -1; self.rx_flags = 0; self.tx_flags = 0
        self.tx_underran = False; self.tx_stuck = False; self.tx_stalls = 0; self.tx_restarts = 0
        self.rx_signal = lambda k: 0       # slot index -> 24-bit input sample the ADC delivers
        self.spi_writes = 0
        # McASP AFIFO write FIFO (data port 0x01D02000), timer 1 and the DSP interrupt controller
        self.fifo = []; self.wfifoctl = 0x1004; self.fifo_overflows = 0
        # AFIFO read FIFO: with RENA, every receive slot (from the next frame after it is enabled or RFRST rises)
        # lands in it; the data port read pops it; more than 64 words = overrun (RSTAT.ROVRN)
        self.rfifo = []; self.rfifoctl = 0x1004; self.rfifo_next = None
        # FIFO-mode transmit has the one-slot XBUF stage of the real McASP: slot k goes out from XBUF, and the FIFO's
        # head group moves into XBUF for slot k + 1. tx_skew / rx_skew: groups the transmitter swallows at start, or
        # an odd first receive slot (seen on the R8: left and right came out swapped in some sessions)
        self.xbuf_grp = None; self.tx_skew = 0; self.rx_skew = 0; self.tx_skew_left = 0
        self.tmr = {0x20: 0, 0x24: 5, 0x18: 0x5DC0}; self.tmr_next = None; self.tmr_ticks = 0
        self.intmux = {0x104: 0x08060545, 0x108: 0x0B5B0944, 0x10C: 0x280E0D0C}   # as stock leaves them
        sim.io[0x01D02] = self.dataport; sim.io[0x01C21] = self.timer1; sim.io[0x01800] = self.intc
        sim.on_event = self.event
        # cache control (L1PINV 0x01845028, L1DWBINV 0x01845044, ...): operations finish at once and read 0. The
        # simulator keeps decoded instructions like L1P keeps lines: only L1PINV discards them, so code that copies
        # new code over old without invalidating L1P runs the old code here too.
        self.l1p_invalidates = 0
        self.watchdog_reset = None         # cycle at which software armed the watchdog (the R8 resets into stock)
        sim.io[0x01845] = self.cachectl
        # I2C0 master (polled) with the audio clock chip at 0x54: writes are logged; F5 with family byte 0x47
        # switches the McASP slot clock to 48 kHz, 0x41 back to 44.1 kHz
        self.i2c_regs = {0x24: 0x4220}; self.i2c_st = 0x20; self.i2c_cur = None; self.i2c_log = []
        self.i2c_ack = {0x54}
        self.i2c_busy_until = 0; self.i2c_lost = 0   # a STOP takes ~10 us to finish; a START sent before then is lost
        sim.io[0x01C22] = self.i2c
        for page, h in ((0x01C10, self.psc), (0x01E27, self.psc), (0x01C14, self.syscfg), (0x01E26, self.gpio_io),
                        (0x01F0E, self.spi), (0x01D0D, self.uart), (0x01D00, self.mcasp), (0x01D01, self.mcasp)):
            sim.io[page] = h

    # ---- PSC: everything reports enabled, transitions finish at once
    def psc(self, sim, a, size, v):
        o = a & 0xfff; key = (a & ~0xfff, o)
        if not hasattr(self, 'mdctl'): self.mdctl = {}
        if v is None:                              # MDSTAT follows MDCTL at once (modules start enabled)
            if 0x800 <= o < 0x900: return self.mdctl.get((a & ~0xfff, o + 0x200), 3)
            if 0xA00 <= o < 0xB00: return self.mdctl.get(key, 3)
            return 0
        if 0xA00 <= o < 0xB00: self.mdctl[key] = v & 0x1F; self.psc_log = getattr(self, 'psc_log', []) + [(a, v & 0x1F)]
    def syscfg(self, sim, a, size, v):
        if v is None: return 0
    # ---- GPIO: OUT_DATA/SET/CLR per bank pair; LCD pins are CS = GP2[14], A0 = GP8[9]
    def gpio_io(self, sim, a, size, v):
        o = a & 0xfff
        pair = ((o - 0x10) // 0x28) * 0x28 + 0x10 if o >= 0x10 else o
        r = o - pair
        if v is None:                              # IN: outputs read back, inputs come from the outside world
            if r != 0x10: return self.gpio.get(o, 0)
            d = self.gpio.get(pair, 0xFFFFFFFF)
            return (self.gpio.get(pair + 4, 0) & ~d) | (self.ext.get(pair, 0) & d)
        if r == 0x08: self.gpio[pair + 4] = self.gpio.get(pair + 4, 0) | v
        elif r == 0x0C: self.gpio[pair + 4] = self.gpio.get(pair + 4, 0) & ~v
        else: self.gpio[o] = v
    def i2c(self, sim, a, size, v):
        o = a & 0xfff
        if v is None:
            busy = sim.cycle < self.i2c_busy_until
            if o == 0x08: return 0x10 | self.i2c_st          # ICXRDY always; BB already clear while STP finishes (hardware)
            if o == 0x24: return self.i2c_regs.get(0x24, 0) | (0x800 if busy else 0)   # STP reads 1 until done
            return self.i2c_regs.get(o, 0)
        if o == 0x08: self.i2c_st &= ~v; return
        if o == 0x20 and self.i2c_cur is not None:
            self.i2c_cur.append(v & 0xFF)
            if len(self.i2c_cur) - 1 >= self.i2c_regs.get(0x14, 0) and self.i2c_stp: self.i2c_stop()
            return
        if o == 0x24:
            if v & 0x2000 and sim.cycle < self.i2c_busy_until:  # as on hardware: master mode drops, STT stays pending
                self.i2c_lost += 1; self.i2c_cur = None; self.i2c_regs[0x24] = v & ~0x0C00; return
            if v & 0x2000:                                     # STT: address phase
                sar = self.i2c_regs.get(0x1C, 0); self.i2c_cur = [sar]; self.i2c_stp = bool(v & 0x800)
                self.i2c_st &= ~0x20
                if sar not in self.i2c_ack: self.i2c_st |= 0x02
            elif v & 0x800 and self.i2c_cur is not None: self.i2c_stop()
            self.i2c_regs[0x24] = v & ~0x2800; return
        self.i2c_regs[o] = v
    def i2c_stop(self):
        c = self.i2c_cur; self.i2c_cur = None; self.i2c_st |= 0x20
        self.i2c_busy_until = self.sim.cycle + 3600   # ~10 us of real CPU cycles: code runs unscaled
        if self.i2c_st & 0x02: return                          # NACKed: nothing reached the chip
        self.i2c_log.append(c)
        if c[0] == 0x54 and len(c) == 13 and c[1] == 0xF5: self.fs = 48000 if c[9] == 0x47 else 44100
    def pin(self, pairoff, bit): return (self.gpio.get(pairoff + 4, 0) >> bit) & 1
    # ---- SPI1 -> LCD
    def spi(self, sim, a, size, v):
        o = a & 0xfff
        if v is None: return 0                     # SPIBUF: RXEMPTY clear -> byte done
        if o == 0x3C and self.lcd_ready:
            self.spi_writes += 1
            if self.pin(0x38, 14): return          # CS high: not for the LCD
            b = v & 0xff
            if not self.pin(0xB0, 9):              # A0 low: command
                self.lcd_cmds.append(b)
                if b & 0xF0 == 0xB0: self.page = b & 7
                elif b & 0xF0 == 0x10: self.col = (self.col & 0x0F) | (b & 0x0F) << 4
                elif b & 0xF0 == 0x00: self.col = (self.col & 0xF0) | (b & 0x0F)
            else:
                if self.col < 128: self.lcd[self.page * 128 + self.col] = b
                self.col += 1
    lcd_ready = True
    # ---- UART2 <-> panel
    def uart(self, sim, a, size, v):
        o = a & 0xfff
        if v is None:
            if o == 0x14: return 0x60 | (1 if self.rx and self.rx[0][0] <= sim.cycle else 0)
            if o == 0x00 and self.rx and self.rx[0][0] <= sim.cycle: return self.rx.pop(0)[1]
            return 0
        if o == 0x00:
            self.tx.append(v & 0xff)
            if self.tx[-4:] == [0x9E, 1, 0, 0xF7]:       # snapshot request: E0 + 22 bytes about 6 ms later
                dump = [0xE0] + [0] * 8 + [127, 0, 101] + list(self.panel_pos) + [1, 120]
                t0 = sim.cycle + int(0.006 * self.cpu_hz); per = int(self.cpu_hz / 16000)
                for i, b in enumerate(dump): self.rx.append((t0 + i * per, b))
                self.rx.sort(key=lambda x: x[0])
    def send(self, at_s, data):
        c = int(at_s * self.cpu_hz)                   # at_s is in simulated (firmware) seconds
        for b in data: self.rx.append((c, b))
        self.rx.sort(key=lambda x: x[0])
    # ---- McASP0 transmit: XSTAT.XDATA is set when the next slot is due
    def slot_cycles(self): return self.cpu_hz / (self.fs * self.slots)
    def rx_running(self): return self.clock and self.gbl & (1 << 4) and self.rx_start is not None
    def rx_latest(self, sim):
        """Receive slots are indexed by time from RFRST: slot k is complete (in RBUF) one slot period after it starts."""
        return int((sim.cycle - self.rx_start) // self.slot_cycles()) - 1
    def rx_check(self, sim):
        """A slot is overrun once the slot after it is complete too and it was never read."""
        k = self.rx_latest(sim)
        if k >= self.rx_last + 2:
            self.rx_overruns += k - self.rx_last - 1; self.rx_flags |= 1; self.rx_last = k - 1
    def tx_check(self, sim):
        """Slots whose time passed without an XBUF write went out empty (underrun). As measured on the R8: after an
        underrun the next XBUF write stalls the transmitter (XDATA never sets again, no more underruns are flagged)
        until XFRST is cleared and set again."""
        if self.tx_stuck: return
        due = self.mcasp_start + len(self.samples) * self.slot_cycles()
        if sim.cycle > due + self.slot_cycles():
            missed = int((sim.cycle - due) // self.slot_cycles())
            self.late += missed; self.late_at.append((len(self.samples), sim.cycle, due)); self.tx_flags |= 1; self.tx_underran = True
            self.samples += [(None, 0)] * missed; self.xbuf4 += [0] * missed
    def cachectl(self, sim, a, size, v):
        if v is None: return 0
        if a & 0xfff == 0x028 and v & 1: sim.code.clear(); self.l1p_invalidates += 1
    def fifo_on(self): return self.wfifoctl & (1 << 16)
    def fifo_drain(self, sim):
        """With the write FIFO on, slot k (k = 0 at XFRST) takes WNUMDMA words from it at mcasp_start + k slots:
        the first to serializer 2, the second to serializer 4. An empty FIFO at slot time is an underrun."""
        if not (self.fifo_on() and self.clock and self.gbl & (1 << 12)): return
        if self.mcasp_regs.get(0xCC, 0) & 1:              # XEVTCTL.XDATDMA: no request, so the FIFO is never read
            self.fifo_stalled = True
            n_due = int((sim.cycle - self.mcasp_start) // self.slot_cycles()) + 1
            while len(self.samples) < n_due:
                self.late += 1; self.tx_flags |= 1; self.samples.append((None, 0)); self.xbuf4.append(0)
            return
        n_due = int((sim.cycle - self.mcasp_start) // self.slot_cycles()) + 1
        nd = max(1, self.wfifoctl & 0xFF)
        while self.tx_skew_left and len(self.fifo) >= nd: del self.fifo[:nd]; self.tx_skew_left -= 1
        while len(self.samples) < n_due:
            due = self.mcasp_start + len(self.samples) * self.slot_cycles()
            if self.xbuf_grp is None and len(self.fifo) >= nd: self.xbuf_grp = self.fifo[:nd]; del self.fifo[:nd]
            if self.xbuf_grp is not None:
                w = self.xbuf_grp; self.xbuf_grp = None
                if len(self.fifo) >= nd: self.xbuf_grp = self.fifo[:nd]; del self.fifo[:nd]   # XBUF refills at once
                self.samples.append((due, w[0])); self.xbuf4.append(w[1] if nd > 1 else w[0])
            else:
                self.late += 1; self.tx_flags |= 1
                if len(self.late_at) < 20: self.late_at.append((len(self.samples), sim.cycle, due))
                self.samples.append((None, 0)); self.xbuf4.append(0)
    def rfifo_on(self): return self.rfifoctl & (1 << 16)
    def rfifo_sync(self, sim):                       # start taking slots at the next frame boundary (even slot)
        k = self.rx_latest(sim) + 1 if self.rx_running() else 0
        self.rfifo_next = k + (k & 1) + (1 if self.rx_skew else 0)
    def rfifo_fill(self, sim):
        if not (self.rfifo_on() and self.rx_running()) or self.rfifo_next is None: return
        k = self.rx_latest(sim)
        while self.rfifo_next <= k:
            if len(self.rfifo) < 64: self.rfifo.append((self.rx_signal(self.rfifo_next) & 0xFFFFFF) << 8)
            else: self.rx_flags |= 1; self.rx_overruns += 1
            self.rfifo_next += 1
        self.rx_last = k
    def dataport(self, sim, a, size, v):
        if v is None:
            self.rfifo_fill(sim)
            return self.rfifo.pop(0) if self.rfifo else 0
        self.fifo_drain(sim)
        if len(self.fifo) < 64: self.fifo.append(v)
        else: self.fifo_overflows += 1
    def tmr_period(self): return (self.tmr.get(0x18, 0) + 1) * self.cpu_hz / 24e6
    def timer1(self, sim, a, size, v):
        o = a & 0xfff
        if v is None: return self.tmr.get(o, 0)
        if o == 0x28:                                  # WDTCR: the two keys with WDEN arm the watchdog = chip reset
            if v == 0xDA7E4000 and self.tmr.get(0x28) == 0xA5C64000 and (self.tmr.get(0x24, 0) >> 2) & 3 == 2:
                self.watchdog_reset = sim.cycle
        self.tmr[o] = v
        if o == 0x20:
            if (v >> 6) & 3 == 2 and self.tmr.get(0x24, 0) & 1:
                self.tmr_next = sim.cycle + self.tmr_period(); sim.next_event = self.tmr_next
            else: self.tmr_next = None; sim.next_event = None
    def intc(self, sim, a, size, v):
        o = a & 0xfff
        if v is None: return self.intmux.get(o, 0)
        if o in self.intmux: self.intmux[o] = v
    def event(self, sim):
        """timer 1 period: event 40, routed by INTMUX to a CPU interrupt"""
        if self.tmr_next is None: return None
        self.tmr_ticks += 1
        for reg, first in ((0x104, 4), (0x108, 8), (0x10C, 12)):
            for b in range(4):
                if (self.intmux[reg] >> (8 * b)) & 0x7F == 40: sim.raise_int(first + b)
        self.tmr_next += self.tmr_period()
        return self.tmr_next
    def mcasp(self, sim, a, size, v):
        """GBLCTL bits latch only with a master clock; after XFRST a slot is due every slot_cycles; XDATA is set
        while the next slot is due and cleared by the XBUF writes (serializers 2 and 4)."""
        o = a & 0x1fff
        running = self.clock and self.gbl & (1 << 12)
        if v is None:
            if o == 0x044: return self.gbl
            if o == 0x0A0: return self.gbl
            if o == 0x060: return self.gbl
            if o == 0x080 and self.rfifo_on():                   # RSTAT; RTDMSLT = the slot being received is even
                self.rfifo_fill(sim)
                return self.rx_flags | (8 if self.rx_running() and (self.rx_latest(sim) + 1) % 2 == 0 else 0)
            if o == 0x101C: self.rfifo_fill(sim); return len(self.rfifo)     # RFIFOSTS.RLVL
            if o == 0x1018: return self.rfifoctl
            if o == 0x080:                                       # RSTAT
                if not self.rx_running(): return 0
                self.rx_check(sim)
                k = self.rx_latest(sim)                          # newest complete slot; RBUF holds it
                st = 0x20 if k > self.rx_last else 0             # RDATA
                st |= 8 if (k + 1) % 2 == 0 else 0               # RTDMSLT: the slot now being received is even
                return st | self.rx_flags
            if o == 0x204:                                       # RBUF1
                if not self.rx_running(): return 0
                self.rx_check(sim)
                k = self.rx_latest(sim)
                self.rx_last = k; self.rx_reads.append(sim.cycle)
                return (self.rx_signal(k) & 0xFFFFFF) << 8 if k >= 0 else 0
            if o == 0x1014: self.fifo_drain(sim); return len(self.fifo)      # WFIFOSTS.WLVL
            if o == 0x1010: return self.wfifoctl
            if o == 0x0C0 and self.fifo_on():                    # XSTAT; XTDMSLT = the slot on the wire is even
                self.fifo_drain(sim)
                return self.tx_flags | (8 if self.samples and (len(self.samples) - 1) % 2 == 0 else 0)
            if o == 0x0C0:                                       # XSTAT
                if not running: return 0
                self.tx_check(sim)
                if self.tx_stuck: return 0x50 | self.tx_flags
                n = len(self.samples)
                due = self.mcasp_start + n * self.slot_cycles()
                st = 0x20 if sim.cycle >= due else 0             # XDATA
                st |= 8 if (n - 1) % 2 == 0 else 0               # XTDMSLT: the slot now being sent is even
                return st | self.tx_flags
            return self.mcasp_regs.get(o, 0)
        if o == 0x044: self.gbl = v if self.clock else 0
        elif o == 0x1018:                                        # RFIFOCTL: RENA off empties it
            self.rfifo_fill(sim); was = self.rfifo_on(); self.rfifoctl = v
            if not v & (1 << 16): self.rfifo = []
            elif not was: self.rfifo_sync(sim)
        elif o == 0x080 and self.rfifo_on():
            self.rfifo_fill(sim); self.rx_flags &= ~(v & 0x107)
        elif o == 0x080:                                         # RSTAT: write 1 to clear
            if self.rx_running(): self.rx_check(sim)
            self.rx_flags &= ~(v & 0x107)
        elif o == 0x1010:                                        # WFIFOCTL: turning WENA off empties it
            self.fifo_drain(sim)
            self.wfifoctl = v
            if not v & (1 << 16): self.fifo = []; self.xbuf_grp = None; self.tx_skew_left = self.tx_skew
        elif o == 0x0C0 and self.fifo_on():
            self.fifo_drain(sim); self.tx_flags &= ~(v & 0x107)
        elif o == 0x0C0:                                         # XSTAT
            if running: self.tx_check(sim)
            self.tx_flags &= ~(v & 0x107)
        elif o == 0x060:                                         # RGBLCTL: receive bits latch with a clock
            was_rx = self.gbl & (1 << 4)
            if self.clock or v & 0x1F == 0:
                self.gbl = (self.gbl & ~0x1F) | (v & 0x1F)
                rose = v & (1 << 4) and not was_rx
                if v & (1 << 4) and self.rx_start is None: self.rx_start = sim.cycle
                if rose and self.rfifo_on(): self.rfifo_sync(sim)
        elif o == 0x0A0:
            self.fifo_drain(sim)
            if self.clock or v & 0x1F00 == 0:
                was = self.gbl & (1 << 12)
                self.gbl = (self.gbl & ~0x1F00) | (v & 0x1F00)
                if v & (1 << 12) and self.mcasp_start is None: self.mcasp_start = sim.cycle
                elif v & (1 << 12) and not was:                  # restart: a new frame starts on the next frame boundary
                    k = -int(-(sim.cycle - self.mcasp_start) // self.slot_cycles()); k += k & 1
                    if k > len(self.samples):                    # the stalled time went out empty
                        self.samples += [(None, 0)] * (k - len(self.samples)); self.xbuf4 += [0] * (k - len(self.xbuf4))
                    self.tx_stuck = self.tx_underran = False; self.tx_restarts += 1
        elif o == 0x208 or o == 0x210:                           # XBUF2 / XBUF4
            if not running: return                               # priming write before the state machine runs
            self.tx_check(sim)
            if self.tx_stuck: return
            if self.tx_underran:                                 # the write after an underrun stalls the transmitter
                self.tx_stuck = True; self.tx_stalls += 1; return
            if o == 0x210: self.xbuf4.append(v); return
            self.samples.append((sim.cycle, v))
        else:
            self.mcasp_regs[o] = v

    # ---- outputs
    def wav(self, path, shift=8):
        fr = [self.samples[i:i + 2] for i in range(0, len(self.samples) - 1, 2)]
        pcm = bytearray()
        for (c, l), (c2, r) in fr:
            for x in (l, r):
                x = (x >> shift) & 0xffffff
                pcm += x.to_bytes(3, 'little')
        hdr = (b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVE' + b'fmt ' +
               struct.pack('<IHHIIHH', 16, 1, 2, self.fs, self.fs * 6, 6, 24) + b'data' + struct.pack('<I', len(pcm)))
        open(path, 'wb').write(hdr + pcm)
    def png(self, path, scale=4):
        w, h = 128 * scale + 16, 64 * scale + 16
        rows = []
        for y in range(h):
            row = bytearray([0])
            for x in range(w):
                px, py = (x - 8) // scale, (y - 8) // scale
                on = 0 <= px < 128 and 0 <= py < 64 and (self.lcd[(py // 8) * 128 + px] >> (py % 8)) & 1
                row += bytes((25, 35, 45) if on else (205, 215, 190))
            rows.append(bytes(row))
        def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
        open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                               chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))
