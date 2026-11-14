#!/usr/bin/env python3
"""cluster.py START END : functions in [START,END) with external callers/callees"""
import sys,os
sys.path.insert(0,os.path.dirname(__file__))
import finfo,xref
db=finfo.db; G=finfo.G
lo,hi=int(sys.argv[1],16),int(sys.argv[2],16)
fs=[f for f in db['funcs'] if lo<=f<hi]
for f in fs:
    ext=sorted({xref.owner(db,a) for a in db['calls'].get(f,[])}-set(fs))
    exc=sorted(c for c in G['callees'].get(f,[]) if not lo<=c<hi)
    print(f'{finfo.lab(f):28s} {f:#x}  ext-callers={[finfo.lab(x) for x in ext][:6]}  ext-callees={[hex(x) for x in exc][:6]} strs={finfo.strs(f)[:3]}')
