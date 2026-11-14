import sys,struct,glob,collections
P={0x01C00000:'EDMA3_CC0',0x01C08000:'EDMA3_TC0',0x01C08400:'EDMA3_TC1',0x01C10000:'PSC0',0x01C11000:'PLL0',0x01C14000:'SYSCFG0',
0x01C20000:'TIMER0',0x01C21000:'TIMER1',0x01C22000:'I2C0',0x01C23000:'RTC',0x01C40000:'MMCSD0',0x01C41000:'SPI0',0x01C42000:'UART0',
0x01D00000:'McASP0',0x01D01000:'McASP0_AFIFO',0x01D02000:'McASP0_DATA',0x01D04000:'McASP1',0x01D08000:'McASP2',0x01D0C000:'UART1',0x01D0D000:'UART2',
0x01D10000:'McBSP0',0x01D11000:'McBSP1',0x01E00000:'USB0',0x01E25000:'USB1_OHCI',0x01E10000:'HPI',0x01E13000:'LCDC',0x01E16000:'uPP',0x01E17000:'VPIF',
0x01E18000:'SATA',0x01E1A000:'PLL1',0x01E1B000:'MMCSD1',0x01E20000:'EMAC',0x01E26000:'GPIO',0x01E27000:'PSC1',0x01E28000:'I2C1',0x01E2C000:'SYSCFG1',
0x01E30000:'EDMA3_CC1',0x01E38000:'EDMA3_TC2',0x01F00000:'eHRPWM0',0x01F06000:'eCAP0',0x01F0C000:'TIMER2',0x01F0D000:'TIMER3',0x01F0E000:'SPI1',
0x68000000:'EMIFA_CTRL',0x60000000:'EMIFA_CS2',0x62000000:'EMIFA_CS3',0x64000000:'EMIFA_CS4',0x66000000:'EMIFA_CS5',0xB0000000:'DDR2_CTRL',0x80000000:'SHARED_RAM'}
def name(v):
    for b,n in P.items():
        if b<=v<b+0x1000 and n not in('EMIFA_CS2','EMIFA_CS3','EMIFA_CS4','EMIFA_CS5','SHARED_RAM'): return n
    for b,n in P.items():
        if n.startswith('EMIFA_CS') and b<=v<b+0x2000000: return n
        if n=='SHARED_RAM' and b<=v<b+0x20000: return n
    return None
lit=collections.Counter(); mv=collections.Counter()
for f in glob.glob(sys.argv[1]+'/sec_*.bin'):
    d=open(f,'rb').read(); n=len(d)//4
    W=[struct.unpack_from('<I',d,i*4)[0] for i in range(n)]
    for w in W:
        k=name(w)
        if k: lit[k]+=1
    # MVKH: bits 2-6 = 0b11010 (MVKH op code field: opcode bits [6:2]=11010) MVKL:01010 ; cst16 in bits 7..22, dst in 23..27
    lo={}
    for i,w in enumerate(W):
        op=(w>>2)&0x1f
        if op==0b01010:   # MVKL/MVK (S-unit)
            lo[(w>>23)&0x1f,(w>>1)&1]=(i,(w>>7)&0xffff)
        elif op==0b11010: # MVKH
            r=((w>>23)&0x1f,(w>>1)&1); hi=(w>>7)&0xffff
            if r in lo and i-lo[r][0]<16:
                v=(hi<<16)|lo[r][1]; k=name(v)
                if k: mv[k]+=1
print('literal words:',dict(lit.most_common()))
print('MVKL/MVKH pairs:',dict(mv.most_common()))
