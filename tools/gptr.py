#!/usr/bin/env python3
"""Map peripheral-base global pointers (initialized via .cinit) to the functions that load them."""
import sys,os,struct,collections
sys.path.insert(0,os.path.dirname(__file__))
import mem,xref,cinit,names,finfo
M=finfo.M; db=finfo.db
P={0x01C00000:'EDMA3_CC0',0x01C08000:'EDMA3_TC0',0x01C08400:'EDMA3_TC1',0x01C10000:'PSC0',0x01C11000:'PLL0',0x01C14000:'SYSCFG0',
0x01C20000:'TIMER0',0x01C21000:'TIMER1',0x01C22000:'I2C0',0x01C23000:'RTC',0x01C40000:'MMCSD0',0x01C41000:'SPI0',0x01C42000:'UART0',
0x01D00000:'McASP0',0x01D01000:'McASP0_AFIFO',0x01D02000:'McASP0_DATA',0x01D04000:'McASP1',0x01D08000:'McASP2',0x01D0C000:'UART1',0x01D0D000:'UART2',
0x01D10000:'McBSP0',0x01D11000:'McBSP1',0x01E00000:'USB0',0x01E13000:'LCDC',0x01E16000:'uPP',0x01E1A000:'PLL1',0x01E1B000:'MMCSD1',
0x01E26000:'GPIO',0x01E27000:'PSC1',0x01E28000:'I2C1',0x01E2C000:'SYSCFG1',0x01E30000:'EDMA3_CC1',0x01E38000:'EDMA3_TC2',0x01F0C000:'TIMER2',0x01F0D000:'TIMER3',0x01F0E000:'SPI1'}
R,_,_=cinit.records(M.S)
glob={}
for a,b in R:
    if len(b)==4:
        v=struct.unpack('<I',b)[0]
        if v in P: glob[a]=P[v]
byval=collections.defaultdict(list)
for a,v,r in db['consts']: byval[v].append(a)
if __name__=='__main__':
    for g,n in sorted(glob.items(),key=lambda x:x[1]):
        fs=sorted({xref.owner(db,a) for a in byval.get(g,[])})
        print(f'{n:13s} ptr@{g:#010x}  {len(fs):3d} funcs: '+', '.join(finfo.lab(f) if f in finfo.LAB else hex(f) for f in fs)[:260])
