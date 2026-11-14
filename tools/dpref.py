#!/usr/bin/env python3
"""B14-relative (DP) global accesses -> functions. B14 = 0xc0812b20 (set in _c_int00)."""
import sys,os,collections,pickle
sys.path.insert(0,os.path.dirname(__file__))
import load,c6x,xref,finfo,gptr
B14=0xc0812b20
CACHE=os.path.join(os.environ.get('R8_WORK','work'),'dp.pkl')
def build():
    S=finfo.S; db=finfo.db; acc=collections.defaultdict(list)
    for b in load.CODE:
        for i in c6x.decode_range(S[b],b):
            if i.kind in('gld','gst') and i.imm[0]==0:  # B14 only
                acc[B14+i.imm[1]].append((i.addr,i.kind))
    pickle.dump(dict(acc),open(CACHE,'wb')); return dict(acc)
ACC=pickle.load(open(CACHE,'rb')) if os.path.exists(CACHE) else build()
if __name__=='__main__':
    db=finfo.db
    for g,n in sorted(gptr.glob.items(),key=lambda x:x[1]):
        fs=sorted({xref.owner(db,a) for a,k in ACC.get(g,[])})
        print(f'{n:13s} ptr@{g:#010x} {len(fs):3d} funcs: '+', '.join(finfo.lab(f) if f in finfo.LAB else hex(f) for f in fs)[:300])
