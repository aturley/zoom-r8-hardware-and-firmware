#!/usr/bin/env python3
"""Annotated listing of a function: dis.py ADDR [ADDR...]"""
import sys,os,bisect,collections
sys.path.insert(0,os.path.dirname(__file__))
import load,c6x,xref,mem,names
try:
    import sim674 as _S
except Exception: _S=None
def _better(i):
    """c6x.py leaves many 32-bit opcodes as <hex>; sim674's decoder (which executes them) knows them."""
    if _S is None or i.size!=4 or not i.text.lstrip('[!AB0-9] ').startswith('.') or '<' not in i.text: return i.text
    try: return _S.decode(i.raw, i.addr).text() + '  (s)'
    except Exception: return i.text
M=mem.Mem(); db=xref.get(); S=M.S
T=names.task_entries(M)
LABELS={f:v[0] for f,v in T.items()}
LABELS[0xc07fab00]='_c_int00'
cv={a:v for a,v,r in db['consts']}
def label(a): return LABELS.get(a,f'sub_{a:08x}')
def annot(v):
    s=M.cstr(v)
    if s and len(s)>=3: return f'"{s[:40]}"'
    if v in db['funcs']: return label(v)
    return ''
def listing(f,maxlen=4000):
    F=db['funcs']; k=bisect.bisect_right(F,f); end=F[k] if k<len(F) else f+maxlen
    end=min(end,f+maxlen); b=load.in_sec(S,f)
    print(f'\n;=== {label(f)} @ {f:#010x}  (callers: {len(db["calls"].get(f,[]))}) ===')
    for i in c6x.decode_range(S[b],b,f,end):
        if i.addr<f or i.addr>=end or i.kind=='hdr': continue
        t=_better(i)
        if i.kind=='call': t=t.replace(f'0x{i.target:08x}',label(i.target))
        note=''
        if i.addr in cv:
            v=cv[i.addr]; note=f'  ; ={v:#010x} {annot(v)}'
        print(f'{i.addr:08x}  {t}{note}')
if __name__=='__main__':
    for a in sys.argv[1:]: listing(int(a,16))
