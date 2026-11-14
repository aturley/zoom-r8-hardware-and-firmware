import glob,os,sys
sys.path.insert(0,os.path.dirname(__file__))
import c6x
WORK=os.environ.get('R8_WORK','work')
SEC=os.path.join(WORK,'sections')
def sections():
    S={}
    for f in glob.glob(SEC+'/sec_*.bin'):
        a=int(os.path.basename(f)[4:12],16); S[a]=open(f,'rb').read()
    return dict(sorted(S.items()))
CODE=[int(x,16) for x in os.environ.get('R8_CODE','11800000,c0715f40').split(',')]
def in_sec(S,a):
    for b,d in S.items():
        if b<=a<b+len(d): return b
    return None
def decode_all(S):
    out=[]
    for b in CODE: out+=c6x.decode_range(S[b],b)
    return out
