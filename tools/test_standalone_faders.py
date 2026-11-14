import sys, collections; sys.path.insert(0,'.')
import standalone_faders as F, standalone as S, sim2
fails=[]
def check(n,c):
    print(('PASS ' if c else 'FAIL ')+n)
    if not c: fails.append(n)
blob,a,addr=F.build(loops_per_ms=1)
class HW(sim2.Sim):
    def __init__(s,*x,**y):
        super().__init__(*x,**y)
        s.pins={'CS':0,'A0':0,'RST':0}; s.ram=[[0]*132 for _ in range(8)]; s.page=0; s.col=0
        s.cmds=[]; s.bad=[]; s.tx=[]; s.rxq=collections.deque(); s.uart={}
        for r in (S.DIR23,S.DIR8): super().wr32(r,0xFFFFFFFF)
    def rd32(s,ad):
        ad&=0xffffffff
        if ad==F.UART2+0x14: return (1 if s.rxq else 0)|0x60
        if ad==F.UART2: return s.rxq.popleft() if s.rxq else 0
        return super().rd32(ad)
    def wr32(s,ad,v):
        super().wr32(ad,v); ad&=0xffffffff
        if F.UART2<=ad<F.UART2+0x40:
            if ad==F.UART2:
                if s.uart.get(0x30)!=0x6001 or s.uart.get(0x0C)!=3: s.bad.append('TX before UART configured')
                s.tx.append(v&0xff)
            else: s.uart.setdefault('log',[]).append((ad-F.UART2,v)); s.uart[ad-F.UART2]=v
        bits={S.CS_BIT:'CS'} if ad in (S.SET23,S.CLR23) else {S.A0_BIT:'A0',S.RST_BIT:'RST'} if ad in (S.SET8,S.CLR8) else {}
        for m,n in bits.items():
            if v&m: s.pins[n]=1 if ad in (S.SET23,S.SET8) else 0
        if ad==S.SPI1+0x3C and s.rd32(S.SPI1+0x04)&0x01000000:
            b=v&0xff
            if s.pins['CS'] or not s.pins['RST']: s.bad.append(('bad pin state',hex(b)))
            if s.pins['A0']==0:
                s.cmds.append(b)
                if b&0xF0==0xB0: s.page=b&7
                elif b&0xF0==0x10: s.col=(s.col&0x0F)|((b&0xF)<<4)
                elif b&0xF0==0x00: s.col=(s.col&0xF0)|(b&0xF)
            else:
                if s.col<132: s.ram[s.page][s.col]=b
                s.col+=1
    def screen(s): return bytes(s.ram[p][c] for p in range(8) for c in range(128))
s=HW(blob,S.BASE,{})
def run(c):
    try: s.run(s.pc if hasattr(s,'pc') else S.BASE,max_cycles=s.cycle+c)
    except RuntimeError: pass
# the simulator has no resumable pc; run from entry once with hooks injecting bytes at cycle marks
plan=[]   # (cycle, bytes)
boot_dump=bytes.fromhex('e3030b090f000703 02e0000000000000 00007f003e000000 0000000001 79e1000064 e203050605060306 05c10178'.replace(' ',''))
inject=[(2_000_000, boot_dump), (2_600_000, bytes([0xC3,0x40])), (3_000_000, bytes([0xCB,0x7F, 0xC5,0x00, 0xC4,0x10,0xC4,0x20,0xC4,0x30]))]
snap={}
orig_tick=s.tick
def tick():
    orig_tick()
    for cyc,b in inject:
        if s.cycle==cyc: s.rxq.extend(b)
    for cyc in (1_900_000,2_500_000,2_900_000,4_500_000):
        if s.cycle==cyc: snap[cyc]=(s.screen(),list(s.tx),len(s.cmds))
s.tick=tick
try: s.run(S.BASE,max_cycles=4_600_000)
except RuntimeError as e: print('stopped:',e)
check('no pin/order violations', not s.bad or print(s.bad[:4]))
exp_lcd=[c for t,c in S.init_script() if t==S.CMD]
check('LCD init identical to stock sequence', s.cmds[:len(exp_lcd)]==exp_lcd)
check('UART2 config sequence (reset, DLL 67, DLH 0, MDR 0, FCR 0/1/0F, IER 0, MCR 0, LCR 3, PWREMU 6001)',
      s.uart['log'][:11]==[(0x30,0),(0x20,67),(0x24,0),(0x34,0),(0x08,0),(0x08,1),(0x08,0x0F),(0x04,0),(0x10,0),(0x0C,3),(0x30,0x6001)])
check('PSC1 enables GPIO, SPI1, UART2', all(s.rd32(S.PSC1+0xA00+4*n)==3 for n in (3,10,13)))
check('panel start-up bytes replayed exactly', s.tx==F.PANEL_START+F.PANEL_LATER)
scr0,tx0,_=snap[1_900_000]
check('idle screen: title, empty boxes, 9 baselines', scr0==F.model_frame([None]*9,0))
scr1,_,_=snap[2_500_000]
check('after boot dump (no fader msgs): RX box filled, bars unchanged', scr1==F.model_frame([None]*9,1))
scr2,_,_=snap[2_900_000]
check('after C3 40: fader 1 bar, FD box filled', scr2==F.model_frame([0x40]+[None]*8,3))
scr3,_,_=snap[4_500_000]
v=[0x40,0x30,0x00]+[None]*5+[0x7F]
check('after master 7F, fader3 00, fader2 10/20/30 burst: final frame', scr3==F.model_frame(v,3))
check('RX queue fully drained', not s.rxq)
print('cycles',s.cycle,'LCD cmds',len(s.cmds),'TX',len(s.tx))
print('FAILURES:', fails if fails else 'none')
