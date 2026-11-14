import os as _os
_HERE=_os.path.dirname(_os.path.abspath(__file__)); _FW=_os.path.join(_HERE,'..','fw'); _DATA=_os.path.join(_HERE,'..','data')
import sys, struct, random; sys.path.insert(0,'.')
import revtest as R, sim2
blob,a,addr=R.build()
fails=[]
def check(n,c):
    print(('PASS ' if c else 'FAIL ')+n)
    if not c: fails.append(n)
def wav(ch, bits, frames, extra=b'', fmt18=False, seed=1):
    ba=ch*bits//8; rnd=random.Random(seed)
    data=bytes(rnd.randrange(256) for _ in range(frames*ba))
    fmt=struct.pack('<HHIIHH',1,ch,48000,48000*ba,ba,bits)+(b'\0\0' if fmt18 else b'')
    body=b'WAVE'+b'fmt '+struct.pack('<I',len(fmt))+fmt+extra+b'data'+struct.pack('<I',len(data))+data
    return b'RIFF'+struct.pack('<I',len(body))+body, len(body)+8-len(data), ba
def expected(w,doff,ba):
    d=w[doff:]; fr=[d[i:i+ba] for i in range(0,len(d),ba)]
    return w[:doff]+b''.join(reversed(fr))
def run(files):
    s=sim2.Sim(blob,R.CAVE,{})
    fs=dict(files); fds={}; st={'opens':0,'closes':0}
    def cstr(p):
        b=bytearray()
        while s.rd8(p): b.append(s.rd8(p)); p+=1
        return b.decode()
    def m_open(sm):
        path=cstr(sm.R['B4']); mode=sm.R['A6']
        if mode==0 and path not in fs: sm.R['A4']=0xFFFFFFFF; return
        if mode==0x101: fs[path]=b''
        h=len(fds)+3; fds[h]=[path,0]; sm.wr32(sm.R['A4'],h); st['opens']+=1; sm.R['A4']=0
    def m_read(sm):
        h,buf,n,got=sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.R['B6']
        path,pos=fds[h]; chunk=fs[path][pos:pos+n]
        for i,b in enumerate(chunk): sm.wr8(buf+i,b)
        fds[h][1]+=len(chunk); sm.wr32(got,len(chunk)); sm.R['A4']=0
    def m_write(sm):
        h,buf,n,got=sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.R['B6']
        path,pos=fds[h]; data=sm.rdbytes(buf,n); f=fs[path]
        fs[path]=f[:pos]+data+f[pos+n:]; fds[h][1]+=n; sm.wr32(got,n); sm.R['A4']=0
    def m_seek(sm):
        h,off,wh=sm.R['A4'],sm.R['B4'],sm.R['A6']
        assert wh==2; fds[h][1]=off; sm.R['A4']=0
    def m_close(sm): st['closes']+=1; sm.R['A4']=0
    s.mocks={R.OPEN:m_open,R.READ:m_read,R.WRITE:m_write,R.SEEK:m_seek,R.CLOSE:m_close}
    s.R.update(A4=0x1111,B4=0x2222,A6=0x3333,B6=0x4444,B3=0xc0700000)
    for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'): s.R[r]=0x1000+int(r[1:])
    end=s.run(a.labels['rev_hook'],max_cycles=200_000_000)
    regs_ok = end==R.ORIG and s.R['B3']==0xc07000000//0x10*0+0xc0700000 and s.R['B15']==0xc0900000 and \
        (s.R['A4'],s.R['B4'],s.R['A6'],s.R['B6'])==(0x1111,0x2222,0x3333,0x4444) and \
        all(s.R[r]==0x1000+int(r[1:]) for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'))
    stat=fs.get('A:\\REVSTAT.BIN',b'')
    code=struct.unpack_from('<I',stat,4)[0] if len(stat)>=8 else None
    return fs,code,regs_ok,st,stat
for name,(w,doff,ba) in {'16-bit stereo, 3 blocks + partial':wav(2,16,20000),
                          '24-bit mono, odd extra chunk':wav(1,24,9001,extra=b'junk'+struct.pack('<I',5)+b'abcde\0'),
                          '24-bit stereo, 18-byte fmt':wav(2,24,6001,fmt18=True),
                          '16-bit mono, exactly one block':wav(1,16,R.CH//2)}.items():
    fs,code,ok,st,stat=run({'A:\\REVIN.WAV':w})
    out=fs.get('A:\\REVOUT.WAV')
    nblk=-(-(len(w)-doff)//R.CH)
    check(f'{name}: status 0, output = frame-reversed input', code==0 and out==expected(w,doff,ba))
    check(f'{name}: stat fields, regs restored, files closed', ok and st['opens']==st['closes']==3 and
          struct.unpack_from('<4sIIIII',stat)==(b'R8RV',0,doff,len(w)-doff,ba,nblk))
fs,code,ok,st,_=run({})
check('no REVIN.WAV: status 1, no output, VERSION continues', code==1 and 'A:\\REVOUT.WAV' not in fs and ok and st['opens']==st['closes'])
fs,code,ok,_,_=run({'A:\\REVIN.WAV':b'NOTAWAVEFILE'*10})
check('not WAVE: status 2', code==2 and 'A:\\REVOUT.WAV' not in fs and ok)
w,doff,ba=wav(1,16,3000,extra=b'LIST'+struct.pack('<I',50000)+bytes(range(256))*195+bytes(80))
fs,code,ok,st,_=run({'A:\\REVIN.WAV':w})
check('50 KB chunk before the audio: status 0, header copied, audio reversed', code==0 and fs.get('A:\\REVOUT.WAV')==expected(w,doff,ba) and ok and st['opens']==st['closes'])
w,_,_=wav(2,16,10)
nodata=w[:36]+b'LIST'+struct.pack('<I',4)+b'abcd'
fs,code,ok,_,_=run({'A:\\REVIN.WAV':nodata})
check('no data chunk at all: status 3, no output', code==3 and 'A:\\REVOUT.WAV' not in fs and ok)
fs,code,ok,_,_=run({'A:\\REVIN.WAV':b'RIFF'+struct.pack('<I',4)+b'WAVE'})
check('truncated after WAVE: status 3', code==3 and ok)
_rp=_os.path.join(_DATA,'REVIN_R8.WAV')
if not _os.path.exists(_rp):
    print('SKIP R8 recording test (put the R8-made REVIN.WAV in data/REVIN_R8.WAV)'); print('FAILURES:', fails if fails else 'none'); raise SystemExit(1 if fails else 0)
real=open(_rp,'rb').read()
fs,code,ok,st,stat=run({'A:\\REVIN.WAV':real})
check('the R8 recording (384 KB LIST chunk): status 0, byte-exact output', code==0 and fs.get('A:\\REVOUT.WAV')==expected(real,393216,4) and ok)
print('R8 stat:', struct.unpack_from('<4sIIIII',stat))
print('FAILURES:', fails if fails else 'none')
