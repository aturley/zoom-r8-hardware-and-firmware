#!/usr/bin/env python3
"""menudump firmware, but the flash-loaded test program is C compiled with cl6x (cbuild/selftest.c).
Expected on the card after opening TRACK EDIT once: LOADTEST.BIN = 'R8LT' + 0x600DC0D7 (and MENUDUMP.BIN as before).
Usage: python3 cloadtest.py STOCK_R8SYS.BIN cbuild/out/selftest.bin OUT/R8SYS.BIN"""
import os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menudump as M
from faderbar import STOCK_SHA
src, blobf, dst = sys.argv[1:4]
stock = open(src, 'rb').read()
assert hashlib.sha256(stock).hexdigest().startswith(STOCK_SHA), 'not stock v1.17'
blob = open(blobf, 'rb').read()
out, (co, cl), so, bl = M.patch(stock, blob)
os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
open(dst, 'wb').write(out)
print(f'C program {bl} bytes at flash {M.BLOB_FLASH:#x}; sha256 {hashlib.sha256(out).hexdigest()}')
