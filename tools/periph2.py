import sys,os,struct,collections
sys.path.insert(0,os.path.dirname(__file__))
import mem,xref,names
P={0x01C00000:'EDMA3_CC0',0x01C08000:'EDMA3_TC0',0x01C08400:'EDMA3_TC1',0x01C10000:'PSC0',0x01C11000:'PLL0',0x01C14000:'SYSCFG0',
0x01C20000:'TIMER0',0x01C21000:'TIMER1',0x01C22000:'I2C0',0x01C23000:'RTC',0x01C40000:'MMCSD0',0x01C41000:'SPI0',0x01C42000:'UART0',
0x01D00000:'McASP0',0x01D01000:'McASP0_AFIFO',0x01D02000:'McASP0_DATA',0x01D04000:'McASP1',0x01D08000:'McASP2',0x01D0C000:'UART1',0x01D0D000:'UART2',
0x01D10000:'McBSP0',0x01D11000:'McBSP1',0x01E13000:'LCDC',0x01E16000:'uPP',0x01E1A000:'PLL1',0x01E1B000:'MMCSD1',
0x01E26000:'GPIO',0x01E27000:'PSC1',0x01E28000:'I2C1',0x01E2C000:'SYSCFG1',0x01E30000:'EDMA3_CC1',0x01E38000:'EDMA3_TC2',0x01F0C000:'TIMER2',0x01F0D000:'TIMER3',0x01F0E000:'SPI1'}
def pname(v):
    for b,n in P.items():
        if b<=v<b+(0x8000 if n.startswith('EDMA3_CC') else 0x1000): return n
M=mem.Mem(); db=xref.get(); T=names.task_entries(M)
byval=collections.defaultdict(list)
for a,v,r in db['consts']: byval[v].append(a)
res=collections.defaultdict(set)
for b,d in M.regions:
    if b in (0x11800000,0xc0715f40): continue
    for o in range(0,len(d)-3,4):
        w=struct.unpack_from('<I',d,o)[0]; n=pname(w)
        if not n: continue
        loc=b+o
        # code referencing this word or the start of a struct up to 256B before it
        for back in range(0,260,4):
            for a in byval.get(loc-back,[]):
                res[n].add((xref.owner(db,a),loc-back,back))
for n in sorted(res):
    fs=sorted({f for f,_,_ in res[n]})
    print(f'{n:12s} via data: '+', '.join(f'{f:#x}'+('['+T[f][0]+']' if f in T else '') for f in fs)[:300])
