import sys,glob,collections,math
for f in sorted(glob.glob(sys.argv[1]+'/*.bin')):
    d=open(f,'rb').read()
    n=len(d)//4
    if n==0: continue
    top=collections.Counter(d[i*4+3]>>4 for i in range(n))
    armcond=top[0xe]/n
    # C6000 fetch packet header: top nibble 0xE every 8th word with p-bits; check word7 of 32B packets
    fph=sum(1 for i in range(0,len(d)-31,32) if d[i+31]>>4==0xe)/max(1,len(d)//32)
    ent=-sum(c/len(d)*math.log2(c/len(d)) for c in collections.Counter(d).values())
    print(f'{f.split("/")[-1]:24s} {len(d):8d}B  ARM-cond(0xE)={armcond:.2f}  C64x+ FPhdr={fph:.2f}  entropy={ent:.2f}')
