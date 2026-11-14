import struct, sys, os
d = open(sys.argv[1],'rb').read()
out = sys.argv[2] if len(sys.argv)>2 else None
names = {0x58535901:'SectionLoad',0x58535902:'RequestCRC',0x58535903:'EnableCRC',0x58535904:'DisableCRC',
 0x58535905:'Jump',0x58535906:'JumpClose',0x58535907:'Set',0x58535908:'StartOver',0x58535909:'CmpSecLoad',
 0x5853590A:'SectionFill',0x5853590B:'Ping',0x5853590C:'Get',0x5853590D:'FunctionExec',0x58535963:'SeqReadEnable'}
u=lambda o: struct.unpack_from('<I',d,o)[0]
off=0x40
assert u(off)==0x41504954; off+=4
secs=[]
while off < len(d)-4:
    c=u(off); n=names.get(c)
    if n is None:
        print(f'{off:#09x} UNKNOWN word {c:#010x}; stopping'); break
    off+=4
    if n=='SectionLoad' or n=='CmpSecLoad':
        a,s=u(off),u(off+4); 
        if n=='CmpSecLoad': print(f'{off-4:#09x} {n} {[hex(u(off+4*i)) for i in range(4)]}'); break
        print(f'{off-4:#09x} SectionLoad addr={a:#010x} size={s:#x} ({s})'); secs.append((a,s,off+8))
        off+=8+((s+3)&~3)
    elif n=='FunctionExec':
        w=u(off); fn=w&0xffff; na=w>>16; args=[u(off+4+4*i) for i in range(na)]
        print(f'{off-4:#09x} FunctionExec fn={fn} args={[hex(x) for x in args]}'); off+=4+4*na
    elif n=='SectionFill':
        a,s,t,p=[u(off+4*i) for i in range(4)]; print(f'{off-4:#09x} SectionFill addr={a:#x} size={s:#x} pattern={p:#x}'); off+=16
    elif n=='Set':
        t,a,v,dl=[u(off+4*i) for i in range(4)]; print(f'{off-4:#09x} Set type={t:#x} addr={a:#010x} val={v:#010x} delay={dl}'); off+=16
    elif n=='RequestCRC':
        print(f'{off-4:#09x} RequestCRC crc={u(off):#010x} seek={struct.unpack_from("<i",d,off+4)[0]}'); off+=8
    elif n in('Jump','JumpClose'):
        print(f'{off-4:#09x} {n} entry={u(off):#010x}'); off+=4
        if n=='JumpClose': break
    else:
        print(f'{off-4:#09x} {n}')
print(f'AIS ends at {off:#x}; file size {len(d):#x}; trailing {len(d)-off} bytes')
if out:
    os.makedirs(out,exist_ok=True)
    for a,s,o in secs: open(f'{out}/sec_{a:08x}.bin','wb').write(d[o:o+s])
