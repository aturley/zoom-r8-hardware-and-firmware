import os as _os
_HERE=_os.path.dirname(_os.path.abspath(__file__)); _FW=_os.path.join(_HERE,'..','fw'); _DATA=_os.path.join(_HERE,'..','data')
import sys, struct, random; sys.path.insert(0,'.')
import menudump as M, sim2
fails=[]
def check(n,c):
    print(('PASS ' if c else 'FAIL ')+n)
    if not c: fails.append(n)
stock=open(_os.path.join(_FW,'R8SYS.BIN'),'rb').read()
img,_,_,bl=M.patch(stock)
flash=bytearray(b'\xff'*0x800000); flash[0x80000:0x80000+len(img)-0x40]=img[0x40:]
cave,a=M.build_cave(bl)
for clob in (0xBADBAD00,0,0xFFFFFFFF):
    s=sim2.Sim(cave,M.CAVE,{}); s.clobber=clob; s.far_mocks=True
    rnd=random.Random(5)
    ram={}
    for base,size in M.DUMPS:
        blk=bytes(rnd.randrange(256) for _ in range(size)); ram[base]=blk
        for i,b in enumerate(blk): s.mem[base+i]=b
    for i in range(0,0x100,4): s.wr32(M.VARS+i,0xA5A5A5A5)   # idle RAM is not zero at power-on
    s.wr32(M.TRACK_IDX,3)
    ram={base:s.rdbytes(base,size) for base,size in M.DUMPS}
    fs={}; fds={}; order=[]
    def cstr(p):
        b=bytearray()
        while s.rd8(p): b.append(s.rd8(p)); p+=1
        return b.decode()
    def m_open(sm):
        path=cstr(sm.R['B4']); fs[path]=b''; h=len(fds)+5; fds[h]=path; sm.wr32(sm.R['A4'],h); sm.R['A4']=0; order.append('open '+path)
    def m_write(sm):
        h,buf,n,w=sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.R['B6']; fs[fds[h]]+=sm.rdbytes(buf,n); sm.wr32(w,n); sm.R['A4']=0
    def m_close(sm): order.append('close'); sm.R['A4']=0
    def m_flash(sm):
        ad,buf,n=sm.R['A4'],sm.R['B4'],sm.R['A6']; data=bytes(flash[ad:ad+n]); sm.add_region(buf,data); order.append(('flash',ad,buf,n))
    def m_wbinv(sm): order.append(('wbinv',sm.R['A4'],sm.R['B4'],sm.R['B6']))
    def m_l1p(sm): order.append('l1pinv')
    def m_strlen(sm):
        order.append('strlen'); sm.R['A4']=len(cstr(sm.R['A4']))
    s.mocks={M.OPEN:m_open,M.WRITE:m_write,M.CLOSE:m_close,M.FLASH_READ:m_flash,M.CACHE_WBINV:m_wbinv,M.CACHE_INV_L1P:m_l1p,M.STRLEN:m_strlen}
    def call():
        s.R.update(A4=0xc05e4fc0,B4=0x2222,A6=0,B6=0,B3=0xc072fec4,B15=0xc0900000)
        for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'): s.R[r]=0x1000+int(r[1:])
        s.pending=[]; s.branch=None
        end=s.run(a.labels['dump_hook'],max_cycles=s.cycle+20_000_000)
        return end==M.MENU_ENGINE and (s.R['A4'],s.R['B4'],s.R['A6'],s.R['B6'],s.R['B3'],s.R['B15'])==(0xc05e4fc0,0x2222,0,0,0xc072fec4,0xc0900000) \
            and all(s.R[r]==0x1000+int(r[1:]) for r in ('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13'))
    print('--- clobber',hex(clob))
    check('first call reaches the menu engine with args/return/callee-saved intact', call())
    d=fs.get('A:\\MENUDUMP.BIN',b'')
    hdr=struct.unpack_from('<4sIIIIIII',d)
    check('dump header', hdr==(b'R8MD',0xc05e4fc0,0x2222,0,0,3,0xc05e0000,0xc0812000))
    check('dump body = RAM regions', d[32:]==ram[0xc05e0000]+ram[0xc0812000])
    check('loader: flash read -> wbInv(range) -> L1P inv -> run', [o for o in order if isinstance(o,tuple) or o in ('l1pinv','strlen')][:4]==
          [('flash',M.BLOB_FLASH,M.CODEBUF,bl),('wbinv',M.CODEBUF,bl,1),'l1pinv','strlen'])
    check('LOADTEST.BIN = R8LT + 0x600DC0D7', fs.get('A:\\LOADTEST.BIN')==b'R8LT'+struct.pack('<I',0x600DC0D7))
    n=len(order)
    check('second call: no files, straight to the engine', call() and len(order)==n)
print('FAILURES:', fails if fails else 'none')
