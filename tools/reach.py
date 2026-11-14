#!/usr/bin/env python3
"""Reachability sweep over .text/L2 code: list unreached (dead) instruction runs."""
import sys,os,struct
sys.path.insert(0,os.path.dirname(__file__))
import load,c6x,xref,mem
def sweep():
    M=mem.Mem(); S=M.S; db=xref.get(); I=load.decode_all(S)
    seq=[i for i in I if i.kind!='hdr']
    pos={i.addr:n for n,i in enumerate(seq)}
    # execute-packet index: ep[n] increments after an insn whose p==0
    ep=[];k=0
    for i in seq: ep.append(k); k+= (i.p==0)
    entries=set(db['funcs'])
    for b,d in M.regions:            # any code address stored in data
        if b in load.CODE: continue
        for o in range(0,len(d)-3,4):
            w=struct.unpack_from('<I',d,o)[0]
            if w in pos: entries.add(w)
    for a,v,r in db['consts']:
        if v in pos: entries.add(v)
    reached=bytearray(len(seq)); work=[pos[e] for e in entries if e in pos]
    while work:
        n=work.pop()
        stop_ep=None
        while n<len(seq) and not reached[n]:
            reached[n]=1; i=seq[n]
            if i.kind in('b','call') and i.target in pos: work.append(pos[i.target])
            terminal = i.kind in('ret','bind') or (i.kind=='b' and not i.text.lstrip().startswith('['))
            if terminal and stop_ep is None:
                stop_ep = ep[n] + 6     # branch packet + 5 delay-slot packets
            n+=1
            if stop_ep is not None and n<len(seq) and ep[n]>=stop_ep: break
    runs=[];n=0
    while n<len(seq):
        if not reached[n]:
            m=n
            while m<len(seq) and not reached[m]: m+=1
            a0=seq[n].addr; a1=seq[m].addr if m<len(seq) else seq[-1].addr+4
            nonnop=sum(1 for i in seq[n:m] if i.kind!='nop')
            runs.append((a1-a0,a0,a1,nonnop))
            n=m
        else: n+=1
    return runs
if __name__=='__main__':
    os.environ.setdefault('R8_WORK','work')
    runs=sorted(sweep(),reverse=True)
    print('unreached runs:',len(runs),' total bytes:',sum(r[0] for r in runs))
    for sz,a0,a1,nn in runs[:15]: print(f'{a0:#x}-{a1:#x} {sz:6d} bytes, {nn} non-NOP insns')
