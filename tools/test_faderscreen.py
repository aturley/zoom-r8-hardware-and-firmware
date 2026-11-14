import sys; sys.path.insert(0,'.')
import faderscreen as F, sim2
blob,a=F.build(); L=a.labels
fails=[]
def check(n,c):
    print(('PASS ' if c else 'FAIL ')+n)
    if not c: fails.append(n)
def model(vals):
    """Pixel model: slot s, value v -> rows (63 - v//2)..63 lit in columns 10s+1..10s+8."""
    px=[[0]*128 for _ in range(64)]
    for s,v in enumerate(vals):
        top=63-v//2
        for r in range(top,64):
            for c in range(10*s+1,10*s+9): px[r][c]=1
    fb=bytearray(1024)
    for b in range(8):
        for c in range(128):
            fb[b*128+c]=sum(px[b*8+k][c]<<k for k in range(8))
    return bytes(fb)
BUFP,CNTP=0xc0800000,0xc0800100
for clob in (0xBADBAD00,0,0xFFFFFFFF):
    print('--- clobber',hex(clob))
    s=sim2.Sim(blob,F.CAVE,{}); s.clobber=clob
    puts=[]
    s.wr32(F.NOW_MS,123456)
    def run(byte,n=1,ret=7):
        def m_read(sm):
            assert (sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.R['B6'])==(2,1,BUFP,CNTP)
            sm.wr8(BUFP,byte); sm.wr32(CNTP,n); sm.R['A4']=ret
        def m_put(sm): puts.append((sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.rdbytes(sm.R['A6'],1024)))
        s.mocks={F.UART_READ:m_read,F.LCD_PUT:m_put}
        s.R.update(A4=2,B4=1,A6=BUFP,B6=CNTP,B3=0xc07bbd00)
        for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'): s.R[r]=0x1000+int(r[1:])
        s.pending=[]; s.branch=None
        end=s.run(L['rx_hook'])
        return end==0xc07bbd00 and s.R['A4']==ret and s.R['B15']==0xc0900000 and \
            all(s.R[r]==0x1000+int(r[1:]) for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'))
    vals=[0]*12
    check('nothing read: no draw', run(0x40,n=0) and not puts)
    check('C1 drift while not frozen: no takeover', run(0xC1) and run(0x01) and run(0x78) and not puts and s.rd8(F.FREEZE)==0)
    vals[1]=0x78
    check('fader 1 (C3 40): takeover + draw', run(0xC3) and run(0x40) and len(puts)==1)
    vals[3]=0x40
    check('freeze byte 1, timestamp = now, dirty = 0xFF', (s.rd8(F.FREEZE),s.rd32(F.FREEZE_T),s.rd32(F.DIRTY))==(1,123456,0xFF))
    off,n,src,img=puts[-1]
    check('lcd_put(0,1024,FRAME)', (off,n,src)==(0,1024,F.FRAME))
    check('frame matches pixel model (incl. C1 bar recorded earlier)', img==model(vals))
    s.wr32(F.NOW_MS,130000)
    check('master (CB 7F) full bar', run(0xCB) and run(0x7F) and puts[-1][3]==model(vals[:11]+[0x7F]) and s.rd32(F.FREEZE_T)==130000)
    vals[11]=0x7F
    check('C1 while frozen redraws', run(0xC1) and run(0x10) and len(puts)==3)
    vals[1]=0x10
    check('frame after C1 update', puts[-1][3]==model(vals))
    check('key A0 04 (STOP release) ignored', run(0xA0) and run(0x04) and s.rd8(F.FREEZE)==1 and len(puts)==3)
    check('STOP press gives screen back', run(0xA0) and run(0x03) and s.rd8(F.FREEZE)==0 and len(puts)==3)
    check('C1 after release: no takeover', run(0xC1) and run(0x11) and s.rd8(F.FREEZE)==0 and len(puts)==3)
    vals[1]=0x11
    check('all 9 faders map to slots 3..11', all(run(0xC3+i) and run(i*14) for i in range(9)) and puts[-1][3]==model(vals[:3]+[i*14 for i in range(9)]))
    check('other status (CC, 9x) ignored', run(0xCC) and run(0x22) and run(0x95) and run(0x03) and len(puts)==3+9)
    check('return value 0 passed through', run(0x05,ret=0))
print('FAILURES:', fails if fails else 'none')
