import sys,struct; sys.path.insert(0,'.')
import uartlog as U, sim2
blob,a=U.build(); L=a.labels
fails=[]
def check(name,cond):
    print(('PASS ' if cond else 'FAIL ')+name)
    if not cond: fails.append(name)

def new_sim(mocks, clob=0xBADBAD00):
    s=sim2.Sim(blob,U.CAVE,mocks); s.clobber=clob; return s
def records(s):
    idx=s.rd32(U.IDX); p=U.LOG_START; out=[]
    while p<idx:
        h=s.rdbytes(p,12); assert h[0]==0xA5, hex(p)
        n=h[2]|h[3]<<8; out.append((chr(h[1]),struct.unpack('<Q',h[4:12])[0],s.rdbytes(p+12,n))); p+=12+n
    return out

for clob in (0xBADBAD00,0,0xFFFFFFFF):
    print(f'--- clobber {clob:#x}')
    # 1) TX hook: logs bytes, then tail-jumps to uart_write with args and B3 intact
    got={}
    def m_write(s): pass
    s=new_sim({U.UART_WRITE:m_write},clob)
    buf=0xc0800000; data=b'\x10\x22\x33\x44\x55'
    for i,b in enumerate(data): s.wr8(buf+i,b)
    s.R.update(A4=2,B4=len(data),A6=buf,B6=0xc0800100,B3=0xc07bb750)
    end=s.run(L['tx_hook'])
    check('TX exits to uart_write', end==U.UART_WRITE)
    check('TX args/B3/B15 preserved', (s.R['A4'],s.R['B4'],s.R['A6'],s.R['B6'],s.R['B3'],s.R['B15'])==(2,5,buf,0xc0800100,0xc07bb750,0xc0900000))
    r=records(s); check('TX record content', len(r)==1 and r[0][0]=='T' and r[0][2]==data)
    check('TSC started once', [t for t in s.trace if t[0]=='tscl_start'].__len__()==1)
    # second TX: TSC not restarted, timestamp increases
    s.R.update(A4=2,B4=3,A6=buf,B6=0xc0800100,B3=0xc07bb750); s.run(L['tx_hook'])
    r=records(s)
    check('second TX: no TSC restart, ts increases', len([t for t in s.trace if t[0]=='tscl_start'])==1 and len(r)==2 and r[1][1]>r[0][1] and r[1][2]==data[:3])
    # 2) TX len 0: nothing logged
    s=new_sim({U.UART_WRITE:m_write},clob); s.R.update(A4=2,B4=0,A6=buf,B6=0,B3=0x1234)
    end=s.run(L['tx_hook']); check('TX len 0: no record, still forwards', end==U.UART_WRITE and s.rd32(U.IDX)==U.LOG_START and s.R['B3']==0x1234)
    # 3) RX hook with 1 byte and with 0 bytes
    for cnt in (1,0):
        def m_read(s,cnt=cnt):
            assert s.R['A4']==2 and s.R['B4']==1
            s.wr8(s.R['A6'],0x9C); s.wr32(s.R['B6'],cnt); s.R['A4']=0x77
        s=new_sim({U.UART_READ:m_read},clob); s.R.update(A4=2,B4=1,A6=0xc0800200,B6=0xc0800204,B3=0xc07bbd02)
        end=s.run(L['rx_hook'])
        r=records(s)
        check(f'RX count {cnt}: returns to caller with A4/B15 intact', end==0xc07bbd02 and s.R['A4']==0x77 and s.R['B15']==0xc0900000)
        check(f'RX count {cnt}: records', (r==[] ) if cnt==0 else (len(r)==1 and r[0][0]=='R' and r[0][2]==b'\x9c'))
    # 4) buffer full handling
    for room,expect in ((16,False),(17,True)):
        s=new_sim({U.UART_WRITE:m_write},clob); s.wr32(U.IDX,U.LOG_END-room)
        s.R.update(A4=2,B4=5,A6=buf,B6=0,B3=1); s.run(L['tx_hook'])
        moved=s.rd32(U.IDX)!=U.LOG_END-room
        check(f'room {room} for 17-byte record -> logged={expect}', moved==expect and s.rd32(U.IDX)<=U.LOG_END)
    # absurd length (would wrap)
    s=new_sim({U.UART_WRITE:m_write},clob); s.R.update(A4=2,B4=0xfffffff0,A6=buf,B6=0,B3=1); s.run(L['tx_hook'])
    check('absurd length skipped', s.rd32(U.IDX)==U.LOG_START)
    # 5) flush: header + chunked data, then clear, then original VERSION handler
    for nbytes in (0,40000):
        files=[]; st={'open':0,'close':0}
        def m_open(s): st['open']+=1; s.wr32(s.R['A4'],0x4242); assert s.rdbytes(s.R['B4'],15)==b'A:\\UARTLOG.BIN\x00'; s.R['A4']=0
        def m_wr(s): assert s.R['A4']==0x4242; files.append(s.rdbytes(s.R['B4'],s.R['A6'])); s.wr32(s.R['B6'],s.R['A6']); s.R['A4']=0
        def m_close(s): st['close']+=1; s.R['A4']=0
        s=new_sim({U.OPEN:m_open,U.WRITE:m_wr,U.CLOSE:m_close},clob)
        for i in range(nbytes): s.wr8(U.LOG_START+i,(i*7)&0xff)
        s.wr32(U.IDX,U.LOG_START+nbytes); s.R['B3']=0xc0abc000
        end=s.run(L['flush_hook'])
        hdr=files[0] if files else b''
        body=b''.join(files[1:])
        check(f'flush {nbytes}: exits to VERSION handler with B3/B15', end==U.ORIG_VERSION and s.R['B3']==0xc0abc000 and s.R['B15']==0xc0900000)
        check(f'flush {nbytes}: header', hdr==struct.pack('<4sIII',b'R8UL',2,U.LOG_START,nbytes))
        check(f'flush {nbytes}: body bytes and chunking', body==bytes((i*7)&0xff for i in range(nbytes)) and all(len(f)<=U.CHUNK for f in files[1:]) and len(files)-1==-(-nbytes//U.CHUNK))
        check(f'flush {nbytes}: closed once and log cleared', st==({'open':1,'close':1}) and s.rd32(U.IDX)==U.LOG_START)
    # flush with open failure: no writes, log kept
    def m_open_fail(s): s.R['A4']=9
    s=new_sim({U.OPEN:m_open_fail,U.WRITE:lambda s:None,U.CLOSE:lambda s:None},clob); s.wr32(U.IDX,U.LOG_START+100); s.R['B3']=5
    end=s.run(L['flush_hook'])
    check('flush open-fail: skips to VERSION, log kept', end==U.ORIG_VERSION and s.rd32(U.IDX)==U.LOG_START+100 and not any(t[1]==U.WRITE for t in s.trace))
s=new_sim({U.UART_WRITE:lambda s:None}); s.tsc_base=(5<<32)-50
for k in range(3):
    s.R.update(A4=2,B4=2,A6=0xc0800000,B6=0,B3=1); s.run(L['tx_hook'])
ts=[r[1] for r in records(s)]
check('64-bit timestamps across a TSCL rollover stay increasing', len(ts)==3 and ts[0]<ts[1]<ts[2] and (ts[2]>>32)>=5)
print('\nFAILURES:',len(fails))
