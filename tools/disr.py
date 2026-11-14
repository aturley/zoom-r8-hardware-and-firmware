#!/usr/bin/env python3
"""Annotated listing of an address range, ignoring function boundaries: disr.py START END"""
import sys,os
sys.path.insert(0,os.path.dirname(__file__))
import load,c6x,dis
lo,hi=int(sys.argv[1],16),int(sys.argv[2],16)
b=load.in_sec(dis.S,lo)
for i in c6x.decode_range(dis.S[b],b,lo,hi):
    if i.addr<lo or i.addr>=hi or i.kind=='hdr': continue
    t=i.text
    if i.kind=='call' and i.target is not None: t=t.replace(f'0x{i.target:08x}',dis.label(i.target))
    note=''
    if i.addr in dis.cv: v=dis.cv[i.addr]; note=f'  ; ={v:#010x} {dis.annot(v)}'
    lab=f'{dis.label(i.addr)}:' if i.addr in dis.db['funcs'] else ''
    if lab: print(lab)
    print(f'{i.addr:08x} {"|| " if 0 else ""}{t}{note}')
