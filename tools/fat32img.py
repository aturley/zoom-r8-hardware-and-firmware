#!/usr/bin/env python3
"""Build a small FAT32 volume as a sparse block image (dict block -> 512 bytes) for r8sd.SdModel:
MBR with partition 1 at LBA 8192 (as on the R8's card), long file names, subdirectories, and optionally
fragmented files (cluster chains that skip around), so a FAT reader's chain walking gets tested."""
import struct

class Fat32:
    def __init__(self, part_lba=8192, clusters=66000, spc=8, reserved=32):   # >= 65525 clusters: FAT32 by the spec
        self.part, self.spc, self.res = part_lba, spc, reserved
        self.nclus = clusters
        self.fatsz = (4 * (clusters + 2) + 511) // 512
        self.fat = [0x0FFFFFF8, 0x0FFFFFFF] + [0] * clusters
        self.data_lba = part_lba + reserved + 2 * self.fatsz
        self.blocks = {}
        self.dirs = {'': [2]}                      # path -> cluster chain; root = cluster 2
        self.fat[2] = 0x0FFFFFFF
        self.entries = {'': []}                    # path -> list of 32-byte entries
        self.next_free = 3

    def alloc(self, n, fragment=False):
        out = []
        c = self.next_free
        while len(out) < n:
            if self.fat[c] == 0: out.append(c)
            c += 2 if fragment else 1              # fragmented: every other cluster (gaps used by later files)
            if c >= len(self.fat): c = 3
        for a, b in zip(out, out[1:]): self.fat[a] = b
        self.fat[out[-1]] = 0x0FFFFFFF
        if not fragment: self.next_free = out[-1] + 1
        return out

    @staticmethod
    def short(name, n):
        base, _, ext = name.upper().rpartition('.') if '.' in name else (name.upper(), '', '')
        tail = '~' + str(n)
        base = ''.join(ch for ch in base if ch.isalnum())[:8 - len(tail)] + tail
        ext = ''.join(ch for ch in ext if ch.isalnum())[:3]
        return (base.ljust(8) + ext.ljust(3)).encode('ascii')

    def entry(self, path, name, attr, clus, size):
        ents = self.entries[path]
        fits = name.upper() == name and len(name.partition('.')[0]) <= 8 and len(name.partition('.')[2]) <= 3 and ' ' not in name
        if name in ('.', '..'):
            sn = name.ljust(11).encode('ascii')
        elif fits:
            b, _, e = name.partition('.'); sn = (b.ljust(8) + e.ljust(3)).encode('ascii')
        else:
            sn = self.short(name, len(ents) + 1)
            csum = 0
            for ch in sn: csum = (((csum & 1) << 7) + (csum >> 1) + ch) & 0xFF
            u = name.encode('utf-16-le') + (b'\0\0' if len(name) % 13 else b'')
            u += b'\xff' * (-len(u) % 26)
            parts = [u[i:i + 26] for i in range(0, len(u), 26)]
            for i in range(len(parts), 0, -1):
                p = parts[i - 1]
                ents.append(bytes([i | (0x40 if i == len(parts) else 0)]) + p[0:10] + bytes([0x0F, 0, csum]) + p[10:22] + b'\0\0' + p[22:26])
        ents.append(sn + bytes([attr]) + bytes(8) + struct.pack('<H', clus >> 16) + bytes(4) + struct.pack('<HI', clus & 0xFFFF, size))

    def mkdir(self, path):
        parent, _, name = path.rpartition('/')
        c = self.alloc(1)[0]
        self.dirs[path] = [c]; self.entries[path] = []
        self.entry(path, '.', 0x10, c, 0); self.entry(path, '..', 0x10, 0 if parent == '' else self.dirs[parent][0], 0)
        self.entry(parent, name, 0x10, c, 0)

    def add(self, path, data, fragment=False):
        parent, _, name = path.rpartition('/')
        n = max(1, -(-len(data) // (512 * self.spc)))
        chain = self.alloc(n, fragment)
        for i, c in enumerate(chain):
            chunk = data[i * 512 * self.spc:(i + 1) * 512 * self.spc]
            for s in range(0, len(chunk), 512):
                self.blocks[self.data_lba + (c - 2) * self.spc + s // 512] = chunk[s:s + 512].ljust(512, b'\0')
        self.entry(parent, name, 0x20, chain[0], len(data))
        return [self.data_lba + (c - 2) * self.spc for c in chain]       # first block of each cluster

    def deleted(self, path):                       # a deleted entry, which readers must skip
        self.entries[path].append(b'\xe5' + b'LDFILE WAV' + bytes([0x20]) + bytes(20))

    def image(self):
        for path, ents in self.entries.items():
            raw = b''.join(ents)
            per = 512 * self.spc
            chain = self.dirs[path]
            while len(chain) * per < len(raw) + 32:
                extra = self.alloc(1); self.fat[chain[-1]] = extra[0]; chain.append(extra[0])
            raw = raw.ljust(len(chain) * per, b'\0')
            for i, c in enumerate(chain):
                for s in range(self.spc):
                    self.blocks[self.data_lba + (c - 2) * self.spc + s] = raw[i * per + s * 512:i * per + s * 512 + 512]
        fat = b''.join(struct.pack('<I', v) for v in self.fat).ljust(self.fatsz * 512, b'\0')
        for k in range(2):
            for i in range(self.fatsz):
                self.blocks[self.part + self.res + k * self.fatsz + i] = fat[i * 512:(i + 1) * 512]
        total = self.res + 2 * self.fatsz + self.nclus * self.spc
        bpb = bytearray(512)
        bpb[0:3] = b'\xEB\x58\x90'; bpb[3:11] = b'MSDOS5.0'
        struct.pack_into('<HBHBHHBHHHII', bpb, 11, 512, self.spc, self.res, 2, 0, 0, 0xF8, 0, 63, 255, self.part, total)
        struct.pack_into('<IHHIHH', bpb, 36, self.fatsz, 0, 0, 2, 1, 6)
        bpb[82:90] = b'FAT32   '; bpb[510:512] = b'\x55\xAA'
        self.blocks[self.part] = self.blocks[self.part + 6] = bytes(bpb)       # boot sector and its backup
        fsi = bytearray(512)                                                    # FSInfo: counts unknown
        struct.pack_into('<I', fsi, 0, 0x41615252); struct.pack_into('<III', fsi, 484, 0x61417272, 0xFFFFFFFF, 0xFFFFFFFF)
        struct.pack_into('<I', fsi, 508, 0xAA550000)
        self.blocks[self.part + 1] = bytes(fsi)
        mbr = bytearray(512)
        mbr[0x1BE:0x1CE] = bytes([0, 0, 0, 0, 0x0C, 0, 0, 0]) + struct.pack('<II', self.part, total)
        mbr[510:512] = b'\x55\xAA'
        self.blocks[0] = bytes(mbr)
        return self.blocks, self.part + total
