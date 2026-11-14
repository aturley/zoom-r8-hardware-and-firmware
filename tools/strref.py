import sys,os,re,collections
sys.path.insert(0,os.path.dirname(__file__))
import load,xref
S=load.sections(); db=xref.get()
D=S[0xc0604668]; base=0xc0604668
byval=collections.defaultdict(list)
for a,v,r in db['consts']: byval[v].append(a)
def find(pat):
    res=[]
    for m in re.finditer(pat.encode(),D):
        # walk back to start of C string
        s=m.start()
        while s>0 and 32<=D[s-1]<127: s-=1
        e=D.find(b'\0',m.start())
        txt=D[s:e].decode(errors='replace'); va=base+s
        res.append((va,txt,byval.get(va,[])))
    return res
if __name__=='__main__':
    for va,txt,refs in find(sys.argv[1]):
        own=sorted({xref.owner(db,a) for a in refs})
        print(f'{va:#010x} {txt[:50]!r:52s} refs={len(refs)} funcs={[hex(f) for f in own][:6]}')
