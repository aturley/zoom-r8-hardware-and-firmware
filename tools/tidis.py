#!/usr/bin/env python3
"""Stock-firmware listing with TI's own disassembler (dis6x from the installed CGT), which decodes every C674x
opcode including compact ones.  tidis.py START [END]   (hex; END defaults to START+0x200)
Wraps the bytes in a minimal ELF via mkelf6x.py, borrowing the ISA attributes from our cl6x-built forth.out."""
import sys, os, subprocess, tempfile
H = os.path.dirname(os.path.abspath(__file__)); W = os.path.join(H, '..')
DIS = '/Applications/ti/ti-cgt-c6000_8.5.0.LTS/bin/dis6x'
REF = os.path.join(W, 'cbuild', 'forth', 'out', 'forth.out')
a = int(sys.argv[1], 16) & ~31; b = int(sys.argv[2], 16) if len(sys.argv) > 2 else a + 0x200
b = (b + 31) & ~31
d = os.path.join(W, 'fw', 'sections')
for f in os.listdir(d):
    base = int(f[4:12], 16); blob = open(os.path.join(d, f), 'rb').read()
    if base <= a < base + len(blob): break
else: sys.exit(f'{a:#x} not in any section')
with tempfile.TemporaryDirectory() as t:
    open(f'{t}/s.bin', 'wb').write(blob[a - base:b - base])
    subprocess.run([sys.executable, os.path.join(H, 'mkelf6x.py'), f'{t}/s.bin', f'{a:x}', f'{t}/s.obj', REF], check=True)
    out = subprocess.run([DIS, '-qq', '-e', f'{t}/s.obj'], capture_output=True, text=True).stdout
print(out)
