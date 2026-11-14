#!/usr/bin/env python3
"""Zoom R8 firmware (R8SYS.BIN) tool.

Layout (as observed in v1.17):
  0x00  char[32]  model tag, e.g. "ZOOM/D224"
  0x20  char[16]  version string, e.g. "1.17"
  0x30  u32       1 (unknown; image count/type?)
  0x34  u32       checksum = sum of all bytes from 0x40 to EOF (mod 2^32)
  0x38  u32[2]    0
  0x40  ...       TI AIS boot stream (magic 0x41504954) for a C674x DSP,
                  padded with 0xFF, followed by a PCM sample bank (~0x220040+)

Usage:
  r8fw.py info    R8SYS.BIN
  r8fw.py extract R8SYS.BIN OUTDIR      # dump AIS sections + sample bank
  r8fw.py fixsum  IN.BIN OUT.BIN        # recompute header checksum
"""
import struct, sys, os

AIS = {0x58535901: 'SectionLoad', 0x58535902: 'RequestCRC', 0x58535903: 'EnableCRC',
       0x58535904: 'DisableCRC', 0x58535905: 'Jump', 0x58535906: 'JumpClose',
       0x58535907: 'Set', 0x58535908: 'StartOver', 0x58535909: 'CmpSecLoad',
       0x5853590A: 'SectionFill', 0x5853590B: 'Ping', 0x5853590C: 'Get',
       0x5853590D: 'FunctionExec', 0x58535963: 'SeqReadEnable'}
# OMAP-L138 / C6748 ROM AIS function table
FUNCS = {0: 'PLL0Config', 1: 'PLL1Config', 2: 'PeriphClockConfig', 3: 'mDDR/DDR2Config',
         4: 'EMIFA_SDRAMConfig', 5: 'EMIFA_AsyncConfig', 6: 'PLL+ClockConfig',
         7: 'PSCConfig', 8: 'PinmuxConfig'}


def u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def checksum(d):
    return sum(d[0x40:]) & 0xffffffff


def parse_ais(d, base=0x40):
    if u32(d, base) != 0x41504954:
        raise SystemExit('no AIS magic at 0x40')
    o, cmds = base + 4, []
    while o < len(d) - 4:
        c = u32(d, o); n = AIS.get(c)
        if n is None:
            cmds.append(('UNKNOWN', o, c)); break
        o += 4
        if n == 'SectionLoad':
            a, s = u32(d, o), u32(d, o + 4)
            cmds.append((n, o - 4, a, s, o + 8)); o += 8 + ((s + 3) & ~3)
        elif n == 'FunctionExec':
            w = u32(d, o); na = w >> 16
            cmds.append((n, o - 4, w & 0xffff, [u32(d, o + 4 + 4 * i) for i in range(na)])); o += 4 + 4 * na
        elif n in ('SectionFill', 'Set'):
            cmds.append((n, o - 4, [u32(d, o + 4 * i) for i in range(4)])); o += 16
        elif n == 'RequestCRC':
            cmds.append((n, o - 4, u32(d, o))); o += 8
        elif n in ('Jump', 'JumpClose'):
            cmds.append((n, o - 4, u32(d, o))); o += 4
            if n == 'JumpClose': break
        else:
            cmds.append((n, o - 4))
    return cmds, o


def info(d):
    tag = d[0:0x20].split(b'\0')[0].decode(errors='replace')
    ver = d[0x20:0x30].split(b'\0')[0].decode(errors='replace')
    stored = u32(d, 0x34)
    calc = checksum(d)
    print(f'tag={tag!r} version={ver!r} word30={u32(d,0x30):#x}')
    print(f'checksum stored={stored:#010x} calc={calc:#010x} {"OK" if stored == calc else "MISMATCH"}')
    cmds, end = parse_ais(d)
    for c in cmds:
        if c[0] == 'SectionLoad':
            print(f'  {c[1]:#09x} SectionLoad  {c[2]:#010x}  {c[3]:#9x} bytes')
        elif c[0] == 'FunctionExec':
            print(f'  {c[1]:#09x} FunctionExec {FUNCS.get(c[2], c[2])} {[hex(x) for x in c[3]]}')
        elif c[0] in ('Jump', 'JumpClose'):
            print(f'  {c[1]:#09x} {c[0]} entry={c[2]:#010x}')
        else:
            print(f'  {c[1]:#09x} {c[0]} {c[2:]}')
    p = end
    while p < len(d) and d[p] == 0xff: p += 1
    print(f'AIS end {end:#x}; 0xFF pad to {p:#x}; trailing data {len(d)-p:#x} bytes (sample bank?)')
    return cmds, p


def main():
    if len(sys.argv) < 3: raise SystemExit(__doc__)
    cmd, src = sys.argv[1], sys.argv[2]
    d = open(src, 'rb').read()
    if cmd == 'info':
        info(d)
    elif cmd == 'extract':
        out = sys.argv[3]; os.makedirs(out, exist_ok=True)
        cmds, p = info(d)
        for c in cmds:
            if c[0] == 'SectionLoad':
                open(f'{out}/sec_{c[2]:08x}.bin', 'wb').write(d[c[4]:c[4] + c[3]])
        open(f'{out}/trailer_{p:08x}.bin', 'wb').write(d[p:])
        print('written to', out)
    elif cmd == 'fixsum':
        b = bytearray(d); struct.pack_into('<I', b, 0x34, checksum(b))
        open(sys.argv[3], 'wb').write(b); print(f'checksum -> {u32(b,0x34):#010x}')
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
