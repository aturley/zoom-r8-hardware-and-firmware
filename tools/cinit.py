import sys,os,struct
sys.path.insert(0,os.path.dirname(__file__))
import load
def records(S):
    base=int(os.environ.get('R8_CINIT','c080b080'),16)
    if base not in S: return [],0,0
    d=S[base];o=0;recs=[]
    while o+8<=len(d):
        sz,ad=struct.unpack_from('<iI',d,o)
        if sz<=0: break
        recs.append((ad,d[o+8:o+8+sz])); o+=8+((sz+7)&~7)
    return recs,o,len(d)
if __name__=='__main__':
    S=load.sections(); R,o,n=records(S)
    print('records',len(R),'consumed',o,'of',n)
    lo=min(a for a,_ in R); hi=max(a+len(b) for a,b in R)
    print(f'.data span {lo:#x}-{hi:#x} ({(hi-lo)//1024} KB), bytes {sum(len(b) for _,b in R)}')
    big=sorted(R,key=lambda r:-len(r[1]))[:8]; print('largest:',[(hex(a),len(b)) for a,b in big])
