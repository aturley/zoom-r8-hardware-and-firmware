#!/usr/bin/env python3
import sys,os,bisect,collections,pickle
sys.path.insert(0,os.path.dirname(__file__))
import load,c6x,xref,mem,names
M=mem.Mem(); db=xref.get(); S=M.S; F=db['funcs']
T=names.task_entries(M); LAB={f:v[0] for f,v in T.items()}; LAB[0xc07fab00]='_c_int00'
CG=os.path.join(os.environ.get('R8_WORK','work'),'cg.pkl')
def build_cg():
    callees=collections.defaultdict(set); refs=collections.defaultdict(list)
    for b in load.CODE:
        for i in c6x.decode_range(S[b],b):
            if i.kind=='call': callees[xref.owner(db,i.addr)].add(i.target)
    for a,v,r in db['consts']: refs[xref.owner(db,a)].append(v)
    g=dict(callees=dict(callees),refs=dict(refs)); pickle.dump(g,open(CG,'wb')); return g
G=pickle.load(open(CG,'rb')) if os.path.exists(CG) else build_cg()
def lab(f): return LAB.get(f,f'sub_{f:08x}')
def strs(f):
    out=[]
    for v in G['refs'].get(f,[]):
        s=M.cstr(v)
        if s and len(s)>=3: out.append(s)
    return out
def info(f,depth=0,maxd=1,seen=None):
    seen=seen if seen is not None else set()
    if f in seen: return
    seen.add(f)
    cal=sorted(G['callees'].get(f,[])); ss=strs(f)
    ind='  '*depth
    print(f'{ind}{lab(f)} @{f:#x} callers={len(db["calls"].get(f,[]))} callees={len(cal)} strings={ss[:8]}')
    if depth<maxd:
        for c in cal: info(c,depth+1,maxd,seen)
if __name__=='__main__':
    d=int(sys.argv[2]) if len(sys.argv)>2 else 1
    info(int(sys.argv[1],16),0,d)
