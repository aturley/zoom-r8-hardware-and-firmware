import sys; sys.path.insert(0,'.')
import standalone as S, sim2
fails=[]
def check(n,c):
    print(('PASS ' if c else 'FAIL ')+n)
    if not c: fails.append(n)
blob,a,info=S.build(loops_per_ms=1)
class HW(sim2.Sim):
    def __init__(s,*x,**y):
        super().__init__(*x,**y)
        s.pins={'CS':0,'A0':0,'RST':0}; s.events=[]; s.mmio=[]
        s.ram=[[0]*132 for _ in range(8)]; s.page=0; s.col=0; s.cmds=[]; s.bad=[]
        for r in (S.DIR23,S.DIR8): super().wr32(r,0xFFFFFFFF)
    def wr32(s,addr,v):
        super().wr32(addr,v); addr&=0xffffffff
        if 0x01C00000<=addr<0x02000000: s.mmio.append((addr,v))
        bits={S.CS_BIT:'CS'} if addr in (S.SET23,S.CLR23) else {S.A0_BIT:'A0',S.RST_BIT:'RST'} if addr in (S.SET8,S.CLR8) else {}
        for m,n in bits.items():
            if v&m:
                s.pins[n]=1 if addr in (S.SET23,S.SET8) else 0
                if n=='RST': s.events.append(('RST',s.pins[n]))
        if addr==S.SPI1+0x3C and s.rd32(S.SPI1+0x04)&0x01000000:
            b=v&0xff
            if v>>8: s.bad.append(('dat1 upper bits',hex(v)))
            outs=(s.rd32(S.DIR23)&S.CS_BIT)==0 and (s.rd32(S.DIR8)&(S.A0_BIT|S.RST_BIT))==0
            if not outs: s.bad.append('pins not outputs at first byte')
            if s.pins['CS']: s.bad.append(('byte with CS high',hex(b)))
            if not s.pins['RST']: s.bad.append(('byte during reset',hex(b)))
            if s.pins['A0']==0:
                s.cmds.append(b); s.events.append(('CMD',b))
                if b&0xF0==0xB0: s.page=b&7
                elif b&0xF0==0x10: s.col=(s.col&0x0F)|((b&0xF)<<4)
                elif b&0xF0==0x00: s.col=(s.col&0xF0)|(b&0xF)
            else:
                s.ram[s.page][s.col]=b; s.col+=1
s=HW(blob,S.BASE,{})
try: s.run(S.BASE,max_cycles=4_000_000)
except RuntimeError as e: print('stopped:',e)
check('no protocol violations', not s.bad or print(s.bad[:5]))
check('interrupts off: GIE=0, IER=0, ICR written', (s.creg['CSR']&1)==0 and s.creg['IER']==0 and ('mvc','ICR',0xFFF0) in s.trace)
mm=dict(); order=[]
for ad,v in s.mmio: mm[ad]=v; order.append(ad)
check('PSC1 MDCTL3 and MDCTL10 = enable, GO written', mm.get(S.PSC1+0xA0C)==3 and mm.get(S.PSC1+0xA28)==3 and mm.get(S.PSC1+0x120)==1)
check('KICK unlock before first PINMUX write', order.index(S.SYSCFG0+0x38) < order.index(S.SYSCFG0+0x120))
check('PINMUX values = stock (4 untouched)', all(mm.get(S.SYSCFG0+0x120+4*i)==v for i,v in S.PINMUX.items()) and (S.SYSCFG0+0x130) not in mm)
check('pins outputs, others still inputs', s.rd32(S.DIR23)==0xFFFFFFFF^S.CS_BIT and s.rd32(S.DIR8)==0xFFFFFFFF^(S.A0_BIT|S.RST_BIT))
spi={o:s.rd32(S.SPI1+o) for o in (0,4,0x14,0x48,0x50)}
check('SPI1 regs (GCR0 1, GCR1 0x01000003, PC0 0x0E01, DELAY, FMT0 0x21108)', spi=={0:1,4:0x01000003,0x14:0x0E01,0x48:0x03030000,0x50:0x00021108})
exp=[c for t,c in S.init_script() if t==S.CMD]
check('command stream starts with stock init (incl. AF)', s.cmds[:len(exp)]==exp)
rst=[e for e in s.events if e[0]=='RST']
check('reset: high (idle), then low/high pulse twice', [v for _,v in rst]==[1,0,1,0,1])
first_cmd=next(i for i,e in enumerate(s.events) if e[0]=='CMD')
check('reset finished before first command', all(i<first_cmd for i,e in enumerate(s.events) if e[0]=='RST'))
pages=[c for c in s.cmds[len(exp):len(exp)+24]]
check('frame: 8 x (B0+n, 00, 10)', pages==[x for n in range(8) for x in (0xB0+n,0x00,0x10)])
fb=info['fb']
check('display RAM after frame == test pattern (or heartbeat on)', all(s.ram[p][c]==fb[p*128+c] for p in range(8) for c in range(128) if not (p==0 and 112<=c<124)))
hb=[c for c in s.cmds[len(exp)+24:]]
check('heartbeat writes alternate (B0, 00, 17) segments', len(hb)>=6 and hb[:6]==[0xB0,0x00,0x17]*2)
_,on=S.heartbeat(fb)
states=[]
class HB(HW):
    def wr32(s2,addr,v):
        super().wr32(addr,v)
        if addr==S.SET23 and v&S.CS_BIT and s2.page==0 and s2.col==124: states.append(bytes(s2.ram[0][112:124]))
s2=HB(blob,S.BASE,{})
try: s2.run(S.BASE,max_cycles=6_000_000)
except RuntimeError: pass
_,on=S.heartbeat(fb); off=fb[112:124]
st2=[x for i,x in enumerate(states) if i==0 or x!=states[i-1]]
check('heartbeat alternates on/off', len(st2)>=4 and all(st==(on if i%2==0 else off) for i,st in enumerate(st2)))
print('heartbeat block now', 'on' if bytes(s.ram[0][112:124])==on else 'off' if bytes(s.ram[0][112:124])==fb[112:124] else 'other')
print('commands sent:', len(s.cmds), ' cycles:', s.cycle)
print('FAILURES:', fails if fails else 'none')
