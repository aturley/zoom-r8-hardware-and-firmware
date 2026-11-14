import sys; sys.path.insert(0,'.')
import faderbar as F, sim2
blob,a=F.build(); L=a.labels
fails=[]
def check(n,c):
    print(('PASS ' if c else 'FAIL ')+n)
    if not c: fails.append(n)
BUFP, CNTP = 0xc0800000, 0xc0800100
for clob in (0xBADBAD00, 0, 0xFFFFFFFF):
    print('--- clobber', hex(clob))
    s=sim2.Sim(blob,F.CAVE,{}); s.clobber=clob
    puts=[]
    def run(byte, n=1, ret=0x55):
        def m_read(sm):
            assert (sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.R['B6'])==(2,1,BUFP,CNTP), 'uart_read args changed'
            sm.wr8(BUFP, byte if byte is not None else 0); sm.wr32(CNTP, n); sm.R['A4']=ret
        def m_put(sm):
            puts.append((sm.R['A4'], sm.R['B4'], sm.R['A6'], sm.rdbytes(sm.R['A6'], 128)))
        s.mocks={F.UART_READ:m_read, F.LCD_PUT:m_put}
        s.R.update(A4=2,B4=1,A6=BUFP,B6=CNTP,B3=0xc07bbd00)
        for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'): s.R[r]=0x1000+int(r[1:])
        s.pending=[]; s.branch=None
        end=s.run(L['rx_hook'])
        ok = end==0xc07bbd00 and s.R['A4']==ret and s.R['B15']==0xc0900000 and \
             all(s.R[r]==0x1000+int(r[1:]) for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'))
        return ok
    check('no byte: returns, no draw', run(0x40, n=0) and not puts)
    check('data byte before any status: no draw', run(0x40) and not puts)
    check('other status 0xCB then data: no draw', run(0xCB) and run(0x10) and not puts)
    check('status 0xC3 alone: no draw', run(0xC3) and not puts)
    check('C3 40: one draw', run(0x40) and len(puts)==1)
    off,n,src,img=puts[-1]
    check('lcd_put args (896,128,BUF)', (off,n,src)==(F.PAGE*128,128,F.BUF))
    check('bar 0..64 full, rest empty', img==bytes([0x7E]*65+[0x42]*63))
    check('running status: next data draws again (value 0)', run(0x00) and len(puts)==2 and puts[-1][3]==bytes([0x7E]+[0x42]*127))
    check('value 127 fills all', run(0x7F) and puts[-1][3]==bytes([0x7E]*128))
    check('key press A0 03 after: no draw', run(0xA0) and run(0x03) and len(puts)==3)
    check('return value passed through (0)', run(0x05, ret=0))
print('FAILURES:', fails if fails else 'none')
