import sys,os,struct
sys.path.insert(0,os.path.dirname(__file__))
import load,cinit
class Mem:
    def __init__(s):
        S=load.sections(); s.S=S; s.regions=[(b,d) for b,d in S.items()]
        R,_,_=cinit.records(S); s.regions+=R
    def read(s,a,n):
        for b,d in s.regions:
            if b<=a and a+n<=b+len(d): return d[a-b:a-b+n]
        return None
    def u32(s,a):
        r=s.read(a,4); return None if r is None else struct.unpack('<I',r)[0]
    def cstr(s,a,maxn=64):
        r=s.read(a,1)
        if r is None: return None
        out=b''
        while len(out)<maxn:
            c=s.read(a+len(out),1)
            if not c or c==b'\0': break
            out+=c
        return out.decode(errors='replace') if out and all(32<=x<127 for x in out) else None
