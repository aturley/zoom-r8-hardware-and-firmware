import sys,os,struct,bisect,pickle,collections
sys.path.insert(0,os.path.dirname(__file__))
import load
CACHE=os.path.join(os.environ.get('R8_WORK','work'),'xref.pkl')
def build():
    S=load.sections(); I=load.decode_all(S)
    consts=[]   # (insn_addr, value, reg)
    for b in load.CODE:
        pass
    last={}
    for idx,i in enumerate(I):
        if i.kind in('mvkl','mvk') and i.dst is not None: last[i.dst]=(idx,i.imm & 0xffff)
        elif i.kind=='mvkh' and i.dst in last and idx-last[i.dst][0]<64:
            v=(i.imm<<16)|last[i.dst][1]; consts.append((i.addr,v,i.dst)); del last[i.dst]
    funcs=set(i.target for i in I if i.kind=='call')|{0xc07fab00}
    # function pointers stored in data
    starts={i.addr for i in I if i.kind not in('hdr',)}
    fptr=collections.defaultdict(list)
    for b,d in S.items():
        if b in load.CODE: continue
        for o in range(0,len(d)-3,4):
            w=struct.unpack_from('<I',d,o)[0]
            if w in starts and load.in_sec(S,w) in load.CODE: fptr[w].append(b+o)
    # function addresses loaded by code (callbacks) too
    for a,v,r in consts:
        if v in starts and load.in_sec(S,v) in load.CODE: fptr[v].append(a)
    funcs|=set(fptr)
    calls=collections.defaultdict(list)
    for i in I:
        if i.kind=='call': calls[i.target].append(i.addr)
    db=dict(consts=consts,funcs=sorted(funcs),fptr=dict(fptr),calls=dict(calls))
    pickle.dump(db,open(CACHE,'wb')); return db
def get():
    return pickle.load(open(CACHE,'rb')) if os.path.exists(CACHE) else build()
def owner(db,a):
    F=db['funcs']; k=bisect.bisect_right(F,a)-1; return F[k] if k>=0 else None
if __name__=='__main__':
    db=build(); print('consts',len(db['consts']),'funcs',len(db['funcs']),'fptrs',len(db['fptr']))
