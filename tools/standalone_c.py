#!/usr/bin/env python3
"""Wrap a cl6x program linked for L2 (entry at 0x11800000) into a standalone R8SYS image
(stock header + stock PLL/DDR boot commands + one SectionLoad + JumpClose, as standalone.py).
Usage: python3 standalone_c.py STOCK_R8SYS.BIN PROGRAM.out OUT/R8SYS.BIN"""
import os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import celf, standalone
from faderbar import STOCK_SHA
src, prog, dst = sys.argv[1:4]
stock = open(src, 'rb').read()
assert hashlib.sha256(stock).hexdigest().startswith(STOCK_SHA), 'not stock v1.17'
base, img, syms, top = celf.image(prog)
assert base == standalone.BASE and syms.get('r8_boot') == base, 'entry must be r8_boot at 0x11800000'
assert top <= 0x11830000 - 0x4000, f'program + data reach {top:#x}: too close to the stack'
out, ais_len = standalone.make_image(stock, img)
os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
open(dst, 'wb').write(out)
print(f'program {len(img)} bytes at {base:#x} (data up to {top:#x}); AIS script {ais_len} bytes; sha256 {hashlib.sha256(out).hexdigest()}')
