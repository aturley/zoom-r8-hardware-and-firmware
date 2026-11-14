"""USB0 model for sim674: TI wrapper + Mentor MUSB core in peripheral mode (the registers our driver uses), the
CFGCHIP2 PHY control, and a scripted USB host on the other end of the cable (enumeration like a Mac, then CDC
serial traffic). Register behaviour follows the Mentor/TI programming model: CSR0 SERV bits clear RXPKTRDY and
SETUPEND, TXPKTRDY is cleared by the host taking the packet, SENTSTALL is cleared by writing 0, the wrapper's INTSRCR
latches events (write-1-to-clear INTCLRR), FADDR is cleared by bus reset, endpoint registers are reached via INDEX."""

USB0, CFGCHIP2 = 0x01E00000, 0x01C14184

class Ep:
    def __init__(self):
        self.txmaxp = self.rxmaxp = 0
        self.txpktrdy = 0; self.rxpktrdy = 0
        self.txfifo = bytearray(); self.rxfifo = bytearray()
        self.txcsr_bits = 0; self.rxcsr_bits = 0
        self.sentstall = 0; self.flushes = 0; self.clrdt = 0
        self.txstall = 0; self.rxstall = 0               # SENDSTALL set by the device: the host sees STALL

class Musb:
    def __init__(self, sim, board, cfgchip2=0xEF00, vbus_sensed=True):
        """vbus_sensed=False: like the R8, the controller never sees VBUS (DEVCTL 0x80); the pull-up only reaches the
        cable once CFGCHIP2 forces device mode (OTG mode 2)."""
        self.sim, self.board, self.vbus_sensed = sim, board, vbus_sensed
        self.devctl = 0x80
        self.cfg = cfgchip2; self.cfg_writes = []
        self.ctrlr = 0; self.intsrc = 0; self.power = 0; self.faddr = 0; self.index = 0
        self.r8 = {}; self.r16 = {}; self.wrap = {}
        self.ep = [Ep() for _ in range(5)]
        self.c0 = dict(rx=0, tx=0, dataend=0, sendstall=0, sentstall=0, setupend=0)
        self.fifo_sz = {}; self.log = []
        self.host = None; self.wait = None; self.connected_at = None
        self.out_q = bytearray(); self.in_data = bytearray(); self.in_packets = []
        self.host_addr = 0; self.bulk_on = False; self.errors = []
        self.msc_out = []; self.msc_in = []; self.msc_stalls = []   # endpoint 4: packets host -> device, device -> host
        sim.io[USB0 >> 12] = self.io
        sys_h = sim.io.get(CFGCHIP2 >> 12)
        def sysio(s, a, size, v):
            if a == CFGCHIP2:
                if v is None: return self.cfg | (1 << 17 if self.phy_up() else 0)
                self.cfg = v & ~(1 << 17); self.cfg_writes.append(v); return
            return sys_h(s, a, size, v) if sys_h else (0 if v is None else None)
        sim.io[CFGCHIP2 >> 12] = sysio

    def forced(self): return (self.cfg >> 13) & 3 == 2
    def pullup(self):
        return bool(self.power & 0x40) and self.phy_up() and (self.vbus_sensed or self.forced())
    def phy_up(self):
        c = self.cfg
        return not (c & (1 << 15)) and not (c & (1 << 10)) and not (c & (1 << 9)) and (c & 0xF) == 2

    # ---- register file
    def csr0(self):
        c = self.c0
        return c['rx'] | c['tx'] << 1 | c['sentstall'] << 2 | c['dataend'] << 3 | c['setupend'] << 4 | c['sendstall'] << 5
    def io(self, sim, a, size, v):
        o = a - USB0
        if o < 0x400:                                      # TI wrapper (32-bit)
            if v is None:
                if o == 0x04: r = self.ctrlr; self.ctrlr &= ~1; return r
                if o == 0x20: return self.intsrc
                return self.wrap.get(o, 0)
            if o == 0x04: self.ctrlr = v
            elif o == 0x28: self.intsrc &= ~v
            else: self.wrap[o] = v
            return
        o -= 0x400
        n = self.index; ep = self.ep[n]
        if 0x20 <= o < 0x34:                               # FIFOs
            e = self.ep[(o - 0x20) // 4]; k = (o - 0x20) // 4
            if v is None:
                src = self.c0buf_rx if k == 0 else e.rxfifo
                b = bytes(src[:size]); del src[:size]
                return int.from_bytes(b.ljust(size, b'\0'), 'little')
            dst = self.c0buf_tx if k == 0 else e.txfifo
            dst += (v & ((1 << 8 * size) - 1)).to_bytes(size, 'little'); return
        if v is None:
            if o == 0x00: return self.faddr
            if o == 0x01: return self.power
            if o == 0x0E: return self.index
            if o == 0x60: return (0x99 if (self.vbus_sensed or self.forced()) else 0x80) | (self.devctl & 1)
            if o == 0x12: return self.csr0() if n == 0 else (ep.txpktrdy | (len(ep.txfifo) > 0) << 1 | ep.sentstall << 5 | ep.txcsr_bits)
            if o == 0x16: return ep.rxpktrdy | ep.sentstall << 6 | ep.rxcsr_bits
            if o == 0x18: return len(self.c0buf_rx) if n == 0 else len(ep.rxfifo)
            if o == 0x10: return ep.txmaxp
            if o == 0x14: return ep.rxmaxp
            return self.r16.get((o, n), self.r8.get((o, n), 0))
        if o == 0x00: self.faddr = v & 0x7F
        elif o == 0x01:
            if v & 0x40 and not self.power & 0x40: self.connected_at = sim.cycle
            if not v & 0x40: self.connected_at = None
            self.power = v
        elif o == 0x0E: self.index = v & 0xF
        elif o == 0x60: self.devctl = v
        elif o == 0x12 and n == 0:
            c = self.c0
            if v & 0x40: c['rx'] = 0
            if v & 0x80: c['setupend'] = 0
            if v & 0x02: c['tx'] = 1
            if v & 0x08: c['dataend'] = 1
            if v & 0x20: c['sendstall'] = 1
            if not v & 0x04: c['sentstall'] = 0
        elif o == 0x12:
            if v & 0x08: ep.txfifo.clear(); ep.txpktrdy = 0; ep.flushes += 1
            if v & 0x40: ep.clrdt += 1
            if not v & 0x20: ep.sentstall = 0
            ep.txstall = 1 if v & 0x10 else 0
            if v & 0x10: self.msc_stalls.append(('in', n))
            ep.txcsr_bits = v & 0xA400
            if v & 0x01: ep.txpktrdy = 1
        elif o == 0x16:
            if v & 0x10: ep.rxfifo.clear(); ep.rxpktrdy = 0; ep.flushes += 1
            if v & 0x80: ep.clrdt += 1
            if not v & 0x01: ep.rxpktrdy = 0; ep.rxfifo.clear()
            if not v & 0x40: ep.sentstall = 0
            ep.rxstall = 1 if v & 0x20 else 0
            if v & 0x20: self.msc_stalls.append(('out', n))
            ep.rxcsr_bits = v & 0xA800
        elif o == 0x10: ep.txmaxp = v
        elif o == 0x14: ep.rxmaxp = v
        elif o in (0x62, 0x63, 0x64, 0x66): self.fifo_sz[(o, n)] = v
        else:
            (self.r16 if size == 2 else self.r8)[(o, n)] = v

    c0buf_rx = None; c0buf_tx = None

    # ---- host side
    def start(self, script):
        self.c0buf_rx = bytearray(); self.c0buf_tx = bytearray()
        self.host = script(self)
        self.wait = next(self.host)
    def tick(self):
        """called often from the simulator trace hook"""
        if self.bulk_on: self.bulk()
        while self.host:
            if self.wait:
                kind, arg, deadline, what = self.wait
                if kind == 'until' and not arg():
                    if self.sim.cycle > deadline:
                        self.errors.append(f'timeout: {what}'); self.wait = None
                        try: self.wait = self.host.send(False)
                        except StopIteration: self.host = None
                    return
                if kind == 'delay' and self.sim.cycle < arg: return
            try: self.wait = self.host.send(True)
            except StopIteration: self.host = None; return
    def until(self, fn, what, timeout=2_000_000): return ('until', fn, self.sim.cycle + timeout, what)
    def delay(self, cyc): return ('delay', self.sim.cycle + cyc, 0, '')

    def bus_reset(self):
        self.faddr = 0; self.host_addr = 0
        self.c0.update(rx=0, tx=0, dataend=0, sendstall=0, sentstall=0, setupend=0)
        self.c0buf_rx.clear(); self.c0buf_tx.clear()
        self.intsrc |= 1 << 18; self.log.append(('reset', self.sim.cycle))

    def control(self, setup, out=None, result=None):
        """generator: one control transfer; result dict gets status and data"""
        c = self.c0
        if self.faddr != self.host_addr:
            result['status'] = 'noresp'; self.log.append(('noresp', setup.hex(), self.faddr, self.host_addr)); return
        ok = yield self.until(lambda: not c['rx'], 'EP0 free for SETUP')
        c.update(dataend=0, sendstall=0, tx=0); self.c0buf_tx.clear()
        self.c0buf_rx[:] = setup; c['rx'] = 1; self.intsrc |= 1
        wlen = setup[6] | setup[7] << 8
        data = bytearray()
        if setup[0] & 0x80 and wlen:                       # IN data stage
            while True:
                ok = yield self.until(lambda: c['tx'] or c['sendstall'], f'IN data {setup.hex()}')
                if not ok: result['status'] = 'timeout'; return
                if c['sendstall']:
                    c['sendstall'] = 0; c['sentstall'] = 1; self.intsrc |= 1; result['status'] = 'stall'; return
                pkt = bytes(self.c0buf_tx); self.c0buf_tx.clear(); c['tx'] = 0
                if len(pkt) > 64: self.errors.append(f'EP0 packet of {len(pkt)} bytes')
                data += pkt
                last = len(pkt) < 64 or len(data) >= wlen
                if last != bool(c['dataend']): self.errors.append(f'DATAEND {c["dataend"]} on packet {len(data)}/{wlen} of {setup.hex()}')
                if last:
                    yield self.delay(3000); c['dataend'] = 0; self.intsrc |= 1      # status OUT done
                    break
                self.intsrc |= 1
        elif not setup[0] & 0x80 and wlen:                 # OUT data stage
            ok = yield self.until(lambda: (not c['rx']) or c['sendstall'], f'OUT setup serviced {setup.hex()}')
            if c['sendstall']: c['sendstall'] = 0; c['sentstall'] = 1; self.intsrc |= 1; result['status'] = 'stall'; return
            self.c0buf_rx[:] = out; c['rx'] = 1; self.intsrc |= 1
            ok = yield self.until(lambda: not c['rx'] and c['dataend'], f'OUT data accepted {setup.hex()}')
            if not ok: result['status'] = 'timeout'; return
            yield self.delay(3000); c['dataend'] = 0; self.intsrc |= 1
        else:                                              # no data stage
            ok = yield self.until(lambda: (not c['rx'] and c['dataend']) or c['sendstall'], f'no-data request {setup.hex()}')
            if not ok: result['status'] = 'timeout'; return
            if c['sendstall']: c['sendstall'] = 0; c['sentstall'] = 1; self.intsrc |= 1; result['status'] = 'stall'; return
            yield self.delay(3000); c['dataend'] = 0; self.intsrc |= 1
        result['status'] = 'ok'; result['data'] = bytes(data)

    def bulk(self):
        e2, e1 = self.ep[2], self.ep[1]
        if not e2.rxpktrdy and self.out_q:
            if e2.rxmaxp != 64: self.errors.append(f'bulk OUT with RXMAXP {e2.rxmaxp}')
            e2.rxfifo[:] = self.out_q[:64]; del self.out_q[:64]; e2.rxpktrdy = 1; self.intsrc |= 1 << 10
        if e1.txpktrdy:
            pkt = bytes(e1.txfifo); e1.txfifo.clear(); e1.txpktrdy = 0
            if len(pkt) > 64: self.errors.append(f'bulk IN packet of {len(pkt)} bytes')
            self.in_packets.append(len(pkt)); self.in_data += pkt; self.intsrc |= 1 << 1
        e4 = self.ep[4]
        if not e4.rxpktrdy and self.msc_out and not e4.rxstall:
            if e4.rxmaxp != 64: self.errors.append(f'EP4 OUT with RXMAXP {e4.rxmaxp}')
            e4.rxfifo[:] = self.msc_out.pop(0); e4.rxpktrdy = 1; self.intsrc |= 1 << 12
        if e4.txpktrdy and not e4.txstall:
            pkt = bytes(e4.txfifo); e4.txfifo.clear(); e4.txpktrdy = 0
            if len(pkt) > 64: self.errors.append(f'EP4 IN packet of {len(pkt)} bytes')
            if e4.txmaxp != 64: self.errors.append(f'EP4 IN with TXMAXP {e4.txmaxp}')
            self.msc_in.append(pkt); self.intsrc |= 1 << 4

def setup(bm, req, value, index, length):
    return bytes([bm, req, value & 255, value >> 8, index & 255, index >> 8, length & 255, length >> 8])

def mac_enumeration(results):
    """host script like macOS: reset, short device descriptor, reset, address, descriptors, configure, CDC open"""
    def script(m):
        yield m.until(m.pullup, 'D+ pull-up on the cable', 400_000_000)
        yield m.delay(20000)
        m.bus_reset(); yield m.delay(20000)
        steps = [('dev64', setup(0x80, 6, 0x100, 0, 64), None), ('reset', None, None),
                 ('addr', setup(0x00, 5, 7, 0, 0), None), ('dev', setup(0x80, 6, 0x100, 0, 18), None),
                 ('cfg9', setup(0x80, 6, 0x200, 0, 9), None), ('cfg', setup(0x80, 6, 0x200, 0, 255), None),
                 ('str0', setup(0x80, 6, 0x300, 0, 255), None), ('str2', setup(0x80, 6, 0x302, 0x409, 255), None),
                 ('str3', setup(0x80, 6, 0x303, 0x409, 255), None), ('qual', setup(0x80, 6, 0x600, 0, 10), None),
                 ('status', setup(0x80, 0, 0, 0, 2), None),
                 ('setcfg', setup(0x00, 9, 1, 0, 0), None), ('getcfg', setup(0x80, 8, 0, 0, 1), None),
                 ('linecoding', setup(0x21, 0x20, 0, 0, 7), bytes([0x00, 0xC2, 0x01, 0x00, 0, 0, 8])),
                 ('getline', setup(0xA1, 0x21, 0, 0, 7), None),
                 ('dtr', setup(0x21, 0x22, 3, 0, 0), None),
                 ('maxlun', setup(0xA1, 0xFE, 0, 2, 1), None)]
        for name, s, out in steps:
            if name == 'reset': m.bus_reset(); yield m.delay(20000); continue
            r = {}
            yield from m.control(s, out, r)
            results[name] = r
            if name == 'addr' and r.get('status') == 'ok':
                m.host_addr = 7
                yield m.delay(5000)
            if name == 'setcfg' and r.get('status') == 'ok': m.bulk_on = True
            yield m.delay(5000)
        results['done'] = True
    return script

def cbw(tag, xfer, dir_in, cb):
    import struct
    cb = bytes(cb)
    return struct.pack('<4sIIBBB', b'USBC', tag, xfer, 0x80 if dir_in else 0, 0, len(cb)) + cb.ljust(16, b'\0')

def msc_transfer(m, tag, xfer, dir_in, cb, out_data=b'', timeout=60_000_000):
    """generator: one BOT command; returns (data, csw dict or None). Host side as macOS/Linux do it: CBW, data phase
    until xfer bytes or a short packet, then the 13-byte CSW."""
    import struct
    m.msc_out.append(cbw(tag, xfer, dir_in, cb))
    if not dir_in and xfer:
        for i in range(0, xfer, 64): m.msc_out.append(out_data[i:i + 64].ljust(min(64, xfer - i), b'\0'))
    data = bytearray()
    if dir_in and xfer:
        while True:
            ok = yield m.until(lambda: m.msc_in or m.ep[4].txstall, f'MSC data tag {tag}', timeout)
            if not ok or m.ep[4].txstall: return bytes(data), None
            p = m.msc_in.pop(0); data += p
            if len(data) > xfer: m.errors.append(f'MSC tag {tag}: {len(data)} bytes for {xfer}')
            if len(data) >= xfer or len(p) < 64: break
    ok = yield m.until(lambda: m.msc_in or m.ep[4].txstall, f'MSC CSW tag {tag}', timeout)
    if not ok or m.ep[4].txstall: return bytes(data), None
    p = m.msc_in.pop(0)
    if len(p) != 13 or p[:4] != b'USBS': m.errors.append(f'MSC tag {tag}: bad CSW {p[:16].hex()}'); return bytes(data), None
    sig, t, res, st = struct.unpack('<4sIIB', p)
    if t != tag: m.errors.append(f'MSC CSW tag {t} for {tag}')
    return bytes(data), dict(tag=t, residue=res, status=st)
