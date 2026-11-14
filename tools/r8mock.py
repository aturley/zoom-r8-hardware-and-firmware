"""Stand-ins for the stock R8 firmware functions (r8.h / r8_stock.cmd) for sim674: an in-memory card,
the file API with the semantics measured on the unit, string/track/UI helpers. Every call is logged."""
import re

ADDR = {}
for l in open(__import__('os').path.join(__import__('os').path.dirname(__file__), '..', 'cbuild', 'r8_stock.cmd')):
    m = re.match(r'\s*(r8_\w+)\s*=\s*(0x[0-9a-fA-F]+)', l)
    if m: ADDR[m.group(1)] = int(m.group(2), 16)

class R8Env:
    def __init__(self, sim, files=None):
        self.sim, self.fs, self.fds, self.log = sim, dict(files or {}), {}, []
        self.next_fd = 0x100
        self.readonly, self.attached = set(), set()
        for name, a in ADDR.items():
            f = getattr(self, name[3:], None)
            if f: sim.mocks[a] = self._wrap(name, f)

    def _wrap(self, name, f):
        def call(sim):
            r = f(sim, *[sim.arg(i) for i in range(4)])
            self.log.append((name, r)); sim.R[4] = (r or 0) & 0xffffffff
        return call

    # strings
    def strlen(self, sim, p, *a): return len(sim.cstr(p))
    # file API (0 = success)
    def open(self, sim, pfd, ppath, mode, attr):
        path = sim.cstr(ppath)
        if mode == 0x101:
            # env.short_names_only: creating a name that is not 8.3 fails (to test callers' fallbacks)
            base = path.rsplit('\\', 1)[-1]
            if getattr(self, 'short_names_only', False) and not re.fullmatch(r"[A-Z0-9_~$!#&'()@^`{}-]{1,8}(\.[A-Z0-9_~$!#&'()@^`{}-]{1,3})?", base):
                return 6
            self.fs[path] = bytearray()
        elif path not in self.fs: return 1
        if mode in (2, 0x101) and path in self.readonly: return 2
        fd = self.next_fd; self.next_fd += 1
        self.fds[fd] = [path, 0]
        self.fs[path] = bytearray(self.fs[path])
        sim.wr32(pfd, fd); return 0
    def read(self, sim, fd, buf, n, pgot):
        path, pos = self.fds[fd]; d = self.fs[path][pos:pos + n]
        sim.write(buf, bytes(d)); self.fds[fd][1] = pos + len(d); sim.wr32(pgot, len(d)); return 0
    def write(self, sim, fd, buf, n, pn):
        path, pos = self.fds[fd]; f = self.fs[path]
        if len(f) < pos: f.extend(bytes(pos - len(f)))
        f[pos:pos + n] = sim.read(buf, n); self.fds[fd][1] = pos + n; sim.wr32(pn, n); return 0
    def seek(self, sim, fd, off, whence, *a):
        off = off - (1 << 32) if off >> 31 else off
        if whence == 2: self.fds[fd][1] = off
        elif whence == 0: self.fds[fd][1] += off
        else: return 1
        return 0
    def close(self, sim, fd, *a):
        return 0 if self.fds.pop(fd, None) else 1
    def delete(self, sim, p, *a):
        path = sim.cstr(p)
        if path not in self.fs or path in self.attached: return 1
        del self.fs[path]; return 0
    def rename(self, sim, a, b, *x):
        pa, pb = sim.cstr(a), sim.cstr(b)
        if pa not in self.fs or pb in self.fs: return 1
        self.fs[pb] = self.fs.pop(pa); return 0
    def info(self, sim, p, pinfo, *a):
        path = sim.cstr(p)
        if path not in self.fs: return 1
        sim.write(pinfo, bytes(0x140)); sim.write(pinfo + 0x133, bytes([1 if path in self.readonly else 0])); return 0
    # UI
    def pre_msg(self, sim, *a): return 0
    def show_msg(self, sim, p, *a): self.log.append(('msg', sim.cstr(p))); return 0
    def dialog_close(self, sim, ok, *a): return 0
    def popup(self, sim, p, *a): self.log.append(('popup', sim.cstr(p))); return 0
    def card_check(self, sim, what, *a): return 0
    # UI events and the sample rate: env.events(env) gives the next event (0 = none, as a timeout); env.rate
    def ui_event(self, sim, timeout, *a):
        f = getattr(self, 'events', None)
        return f(self) if f else 0
    def sample_rate(self, sim, *a): return getattr(self, 'rate', 44100)
    # cache maintenance (logged so tests can check the order) and the menu engine
    def cache_wbinv(self, sim, addr, n, typ, wait): self.log.append(('wbinv', addr, n, typ, wait)); return 0
    def cache_inv_l1p(self, sim, *a): self.log.append(('l1pinv',)); return 0
    def menu_engine(self, sim, desc, *a): self.log.append(('menu', desc)); return 0
    # folder listing (pattern dir\\* or dir\\*.*), with the find structure's layout as seen on the R8 (UTILDIAG.BIN, 2026-11-12): +0x12 NUL-terminated
    # name, +0x1F long name (env.lfns), +0x11F attributes (0x10 directory, 0x20 archive), +0x120 u32 size. Like FAT, a folder lists '.' and '..'
    # first, then the entries in directory order (the mock uses insertion order); subfolders are listed as directories.
    def find_first(self, sim, ppat, attr, pfind, *a):
        pat = sim.cstr(ppat); d = pat.rsplit('\\', 1)[0] + '\\'
        ents, seen = [('.', True), ('..', True)], set()
        for k in self.fs:
            if not k.upper().startswith(d.upper()): continue
            rest = k[len(d):]; top = rest.split('\\')[0]
            if top and top.upper() not in seen: seen.add(top.upper()); ents.append((top, '\\' in rest))
        if pat.endswith('*.*'):                    # the R8 matches *.* DOS-style: the name needs a dot (LISTDIAG 2026-11-12)
            ents = [e for e in ents if '.' in e[0]]
        self.find_list = ents; self.find_i = 0; self.find_dir = d
        return self._find_put(sim, pfind)
    def find_next(self, sim, pfind, *a): return self._find_put(sim, pfind)
    def find_close(self, sim, pfind, *a): return 0
    def _find_put(self, sim, pfind):
        if self.find_i >= len(getattr(self, 'find_list', [])): return -10406
        name, isdir = self.find_list[self.find_i]; self.find_i += 1
        data = b'' if isdir else self.fs.get(self.find_dir + name, b'')
        rec = bytearray(0x1FC); rec[0x11F] = 0x10 if isdir else 0x20
        lfn = getattr(self, 'lfns', {}).get(self.find_dir + name, '')
        short = name
        if name not in ('.', '..') and not re.fullmatch(r"[A-Z0-9_~$!#&'()@^`{}-]{1,8}(\.[A-Z0-9_~$!#&'()@^`{}-]{1,3})?", name, re.I):
            # a long name, as FAT stores it: an 8.3 alias (NAME~1.EXT) plus the long name
            stem, _, ext = name.rpartition('.') if '.' in name else (name, '', '')
            clean = lambda t: re.sub(r"[^A-Z0-9_~$!#&'()@^`{}-]", '', t.upper())
            short = clean(stem)[:6] + '~1' + ('.' + clean(ext)[:3] if ext else '')
            lfn = lfn or name
        rec[0x120:0x124] = len(data).to_bytes(4, 'little'); rec[0x12:0x12 + len(short)] = short.encode()
        y, mo, dd, h, mi, se = getattr(self, 'mtimes', {}).get(self.find_dir + name, (2026, 11, 12, 7, 3, 20))
        for base in (0x124, 0x138): rec[base:base + 4] = bytes([dd, mo]) + y.to_bytes(2, 'little'); rec[base + 6:base + 9] = bytes([h, mi, se])
        rec[0x11F] |= getattr(self, 'fattrs', {}).get(self.find_dir + name, 0)
        # +0x1F: the long name, or (as on the R8) a 0 first byte with the previous entry's long name left after it
        lfn = lfn.encode()
        self.find_stale = lfn or getattr(self, 'find_stale', b'')
        v = (lfn if lfn else b'\0' + self.find_stale[1:]) + b'\0'
        rec[0x1F:0x1F + len(v)] = v
        sim.write(pfind, bytes(rec)); return 0
