import os as _os
_HERE=_os.path.dirname(_os.path.abspath(__file__)); _FW=_os.path.join(_HERE,'..','fw'); _DATA=_os.path.join(_HERE,'..','data')
import sys, struct; sys.path.insert(0,'.')
import revmenu as R, menudump as MD, sim2
fails=[]
def check(n,c):
    print(('PASS ' if c else 'FAIL ')+n)
    if not c: fails.append(n)
stock=open(_os.path.join(_FW,'R8SYS.BIN'),'rb').read()
img,_,_,bl=R.patch(stock)
flash=bytearray(b'\xff'*0x800000); flash[0x80000:0x80000+len(img)-0x40]=img[0x40:]
cave,a,caddr=R.build_cave(bl)
snap=open(_os.path.join(_DATA,'MENUDUMP.BIN'),'rb').read()
real=open(_os.path.join(_DATA,'REVIN_R8.WAV'),'rb').read()
import menudump as _md
MDTRACK=_md.TRACK_IDX
CONST=open(_os.path.join(_FW,'sections','sec_c0604668.bin'),'rb').read()
PATH='A:\\ZOOM_R8\\PROJECT\\PRJ000\\AUDIO\\MONO-000.WAV'; PATHADDR=0xc05f2d28
def rev(w,doff,ba):
    fr=[w[i:i+ba] for i in range(doff,len(w),ba)]; return w[:doff]+b''.join(reversed(fr))
def mk(clob, files, protected=False, track_has_file=True, stereo_mask=0, track=0):
    s=sim2.Sim(cave,MD.CAVE,{}); s.clobber=clob; s.far_mocks=True
    for i,b in enumerate(snap[32:32+0x20000]): s.mem[0xc05e0000+i]=b
    for i,b in enumerate(snap[32+0x20000:]): s.mem[0xc0812000+i]=b
    s.wr32(MDTRACK, track)
    for i in range(0,0x2000,4): s.wr32(0xc0060000+i,0xA5A5A5A5)
    for i,ch in enumerate(PATH.encode()+b'\0'): s.wr8(PATHADDR+i,ch)
    for i,b in enumerate(CONST[0xc06e7500-0xc0604668:0xc06e7a00-0xc0604668]): s.mem[0xc06e7500+i]=b
    st={'fs':dict(files),'fds':{},'log':[],'attached':{PATH}}
    s.wr32(R.STEREO_MASK, stereo_mask)
    def cstr(p):
        b=bytearray()
        while s.rd8(p): b.append(s.rd8(p)); p+=1
        return b.decode()
    L=st['log']; fs=st['fs']; fds=st['fds']
    def m_open(sm):
        path=cstr(sm.R['B4']); mode=sm.R['A6']
        if mode==0 and path not in fs: sm.R['A4']=0xFFFFFFFF; return
        if mode==0x101: fs[path]=b''
        h=len(fds)+3; fds[h]=[path,0]; sm.wr32(sm.R['A4'],h); sm.R['A4']=0; L.append(('open',path,mode))
    def m_read(sm):
        h,buf,n,got=sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.R['B6']; path,pos=fds[h]; c=fs[path][pos:pos+n]
        for i,b in enumerate(c): sm.wr8(buf+i,b)
        fds[h][1]+=len(c); sm.wr32(got,len(c)); sm.R['A4']=0
    def m_write(sm):
        h,buf,n,got=sm.R['A4'],sm.R['B4'],sm.R['A6'],sm.R['B6']; path,pos=fds[h]; f=fs[path]
        fs[path]=f[:pos]+sm.rdbytes(buf,n)+f[pos+n:]; fds[h][1]+=n; sm.wr32(got,n); sm.R['A4']=0
    def m_seek(sm):
        assert sm.R['A6']==2; fds[sm.R['A4']][1]=sm.R['B4']; sm.R['A4']=0
    def m_close(sm): L.append(('close',fds[sm.R['A4']][0])); sm.R['A4']=0
    def m_delete(sm):                       # like the unit (suspected): a file attached to a track can't be deleted
        p=cstr(sm.R['A4']); L.append(('delete',p))
        if p in st['attached']: sm.R['A4']=1; return
        sm.R['A4']=0 if fs.pop(p,None) is not None else 1
    def m_detach(sm): L.append(('detach',sm.R['A4'])); st['attached'].discard(PATH); sm.R['A4']=0
    def m_reattach(sm):
        L.append(('reattach',sm.R['A4'],sm.R['B4'])); st['attached'].add(cstr(sm.R['A4'])); sm.R['A4']=0
    def m_rename(sm):
        o,n=cstr(sm.R['A4']),cstr(sm.R['B4']); L.append(('rename',o,n))
        if o in fs and n not in fs: fs[n]=fs.pop(o); sm.R['A4']=0
        else: sm.R['A4']=1
    def m_trackfile(sm):
        L.append(('trackfile',sm.R['A4'])); sm.R['A4']=PATHADDR if track_has_file else 0
    def m_info(sm):
        p=cstr(sm.R['A4']); L.append(('info',p))
        if p in fs:
            for i in range(0x140): sm.wr8(sm.R['B4']+i,0)
            sm.wr8(sm.R['B4']+0x133,1 if protected else 0x20); sm.R['A4']=0
        else: sm.R['A4']=1
    def rec(name,ret=0,arg=None):
        def f(sm):
            L.append((name,) + ((cstr(sm.R['A4']),) if arg=='str' else (sm.R['A4'],sm.R['B4']) if arg=='2' else ()))
            sm.R['A4']=ret
        return f
    def m_flash(sm):
        ad,buf,n=sm.R['A4'],sm.R['B4'],sm.R['A6']; sm.add_region(buf,bytes(flash[ad:ad+n])); L.append(('flash',ad,buf,n))
    s.mocks={R.OPEN:m_open,R.READ:m_read,R.WRITE:m_write,R.SEEK:m_seek,R.CLOSE:m_close,R.DELETE:m_delete,R.RENAME:m_rename,
             R.TRACK_FILE:m_trackfile,R.FILE_INFO:m_info,R.REATTACH:m_reattach,R.DETACH:m_detach,R.CLEAR_UNDO:rec('clear_undo'),
             R.CARD_CHECK:rec('card_check'),R.DLG_CLOSE:rec('dlg_close',arg='2'),R.PRE_MSG:rec('pre_msg'),R.SHOW_MSG:rec('show_msg',arg='str'),R.POPUP:rec('popup',arg='str'),
             MD.FLASH_READ:m_flash,MD.CACHE_WBINV:rec('wbinv',arg='2'),MD.CACHE_INV_L1P:rec('l1pinv')}
    return s,st
CSR=('A10','A11','A12','A13','A14','A15','B10','B11','B12','B13')
def run(s,entry,args,ret,mc=80_000_000):
    s.R.update(args); s.R['B3']=ret; s.R['B15']=0xc0900000
    for r in CSR: s.R[r]=0x1000+int(r[1:])
    s.pending=[]; s.branch=None
    end=s.run(a.labels[entry],max_cycles=s.cycle+mc)
    return end, s.R['B15']==0xc0900000 and all(s.R[r]==0x1000+int(r[1:]) for r in CSR)
def w(s,ad): return s.rd32(ad)
for clob in (0xBADBAD00,0,0xFFFFFFFF):
    print('--- clobber',hex(clob))
    s,st=mk(clob,{PATH:real})
    desc=0xc05e4fc0
    end,ok=run(s,'menu_hook',dict(A4=desc,B4=0,A6=0,B6=0),0xc072fec4)
    lst=w(s,desc+32); r2=w(s,lst+8)
    check('menu_hook: continues into the engine with A4=descriptor, B3 intact', end==R.MENU_ENGINE and s.R['A4']==desc and s.R['B3']==0xc072fec4 and ok)
    check('menu now has 3 items; item 3 = REVERSE / rev_item / layout copied from TRIM',
          w(s,desc)==3 and r2==w(s,lst+4)+80 and s.rdbytes(w(s,r2),8)==b'REVERSE\0' and w(s,r2+16)==a.labels['rev_item']
          and w(s,r2+4)==0 and s.rdbytes(r2+8,8)==s.rdbytes(w(s,lst+4)+8,8) and s.rdbytes(r2+20,60)==s.rdbytes(w(s,lst+4)+20,60))
    # second entry: builder rebuilds count=2 but our slot is still there -> accepted again
    s.wr32(desc,2); end,ok=run(s,'menu_hook',dict(A4=desc,B4=0,A6=0,B6=0),0xc072fec4)
    check('re-entry: REVERSE added again', w(s,desc)==3 and end==R.MENU_ENGINE)
    s.wr32(desc+48,5); end,ok=run(s,'menu_hook',dict(A4=desc+48,B4=0,A6=0,B6=0),0xc072fec4)
    check('unexpected layout (count 5): left alone', w(s,desc+48)==5 and end==R.MENU_ENGINE)
    # the REVERSE handler
    end,ok=run(s,'rev_item',dict(A4=0,B4=0),0xc0780000)
    fs=st['fs']; L=st['log']
    check('rev_item returns 0 to the engine, callee-saved intact', end==0xc0780000 and s.R['A4']==0 and ok)
    check('track file reversed in place (R8 recording, byte-exact)', fs.get(PATH)==rev(real,393216,4))
    check('temp file gone (only the log added)', set(fs)=={PATH,'A:\\REVLOG.BIN'})
    names=[e[0] for e in L]
    order=[n for n in names if n in ('card_check','flash','wbinv','l1pinv','trackfile','info','pre_msg','show_msg','clear_undo','delete','rename','reattach')]
    names=[n for n in names]
    check('sequence: card check, load, lookup, info, Please Wait, detach, ..., clear undo, delete, rename, reattach',
          order[:8]==['card_check','flash','wbinv','l1pinv','trackfile','info','pre_msg','show_msg'] and 'detach' in names
          and names.index('detach') < names.index('open') and order[-4:]==['clear_undo','delete','rename','reattach'])
    check('Please Wait message', ('show_msg','Please Wait...') in L)
    check('rename(temp, path) and reattach(path, track 0)', ('rename',PATH[:-1]+'_',PATH) in L and ('reattach',PATHADDR,0) in L)
    check('all files closed', sum(1 for e in L if e[0]=='open')==sum(1 for e in L if e[0]=='close'))
    lg=fs.get('A:\\REVLOG.BIN',b'')
    nv=len(R.VARS); v=dict(zip(R.VARS,struct.unpack_from('<%dI'%nv,lg)))
    check('REVLOG: magic, ERR 0, DOFF/DLEN/BA, path, temp path, call log', v['MAGIC']==0x4c523852 and v['ERR']==0 and
          (v['DOFF'],v['DLEN'],v['BA'])==(393216,519968,4) and lg[4*nv:4*nv+len(PATH)]==PATH.encode() and
          lg[4*nv+128:4*nv+128+len(PATH)]==(PATH[:-1]+'_').encode() and
          struct.unpack_from('<II',lg,4*nv+128+128+64+0x140+32)==(R.TRACK_FILE,PATHADDR) and
          lg[4*nv+128+128+64+0x140:][:16]==rev(real,393216,4)[393216:393216+16]==lg[4*nv+128+128+64+0x140+16:][:16])
    n=len(L); end,ok=run(s,'rev_item',dict(A4=0,B4=0),0xc0780000)
    check('second REVERSE restores the original exactly (code loaded once)', fs.get(PATH)==real and not any(e[0]=='flash' for e in L[n:]) and ok)
    # error paths
    s,st=mk(clob,{PATH:real},track_has_file=False); run(s,'rev_item',dict(A4=0),0xc0780000)
    check('no file on track: "No File!", nothing touched', ('popup','No File!') in st['log'] and {k:v for k,v in st['fs'].items() if k!='A:\\REVLOG.BIN'}=={PATH:real})
    s,st=mk(clob,{PATH:real},protected=True); run(s,'rev_item',dict(A4=0),0xc0780000)
    check('read-only file: "File Protected", nothing touched', ('popup','File Protected') in st['log'] and {k:v for k,v in st['fs'].items() if k!='A:\\REVLOG.BIN'}=={PATH:real})
    s,st=mk(clob,{PATH:b'RIFF'+b'\0'*4+b'JUNK'+b'\0'*60}); run(s,'rev_item',dict(A4=0),0xc0780000)
    check('not a WAV: "Invalid File", original kept, temp removed', ('popup','Invalid File') in st['log'] and {k:v for k,v in st['fs'].items() if k!='A:\\REVLOG.BIN'}=={PATH:b'RIFF'+b'\0'*4+b'JUNK'+b'\0'*60})
    s,st=mk(clob,{PATH:real}); s.mocks[R.CARD_CHECK]=lambda sm: sm.R.__setitem__('A4',5)
    end,_=run(s,'rev_item',dict(A4=0),0xc0780000)
    check('card check fails: return its value, nothing else', s.R['A4']==5 and st['log']==[] and {k:v for k,v in st['fs'].items() if k!='A:\\REVLOG.BIN'}=={PATH:real})
    s,st=mk(clob,{PATH:real},stereo_mask=0b10,track=1); run(s,'rev_item',dict(A4=0),0xc0780000)
    L=st['log']
    check('stereo-linked track 2 -> uses track 1 (index 0) for lookup/detach/reattach',
          ('trackfile',0) in L and ('detach',0) in L and ('reattach',PATHADDR,0) in L and st['fs'][PATH]==rev(real,393216,4))
    s,st=mk(clob,{PATH:real}); s.mocks[R.WRITE]=lambda sm: (sm.wr32(sm.R['B6'],0), sm.R.__setitem__('A4',9))
    run(s,'rev_item',dict(A4=0),0xc0780000); L=st['log']
    check('write error: "File Access Error", original kept and re-attached', ('popup','File Access Error') in L and
          st['fs'].get(PATH)==real and PATH in st['attached'] and not any(k.endswith('_') for k in st['fs']))
    s,st=mk(clob,{PATH:real}); run(s,'rev_item',dict(A4=0),0xc0780000); L=st['log']
    i_show=[e[0] for e in L].index('show_msg')
    check('Please Wait closed with 1 after success', [e for e in L if e[0]=='dlg_close']==[('dlg_close',1,e[2]) for e in L if e[0]=='dlg_close'] and
          len([e for e in L if e[0]=='dlg_close'])==1 and [e[0] for e in L].index('dlg_close')>i_show)
    s,st=mk(clob,{PATH:b'RIFF'+b'\0'*4+b'JUNK'+b'\0'*60}); run(s,'rev_item',dict(A4=0),0xc0780000); L=st['log']; nm=[e[0] for e in L]
    check('error after Please Wait: dialog closed with 0 before the popup', ('dlg_close',0) in [e[:2] for e in L] and
          nm.index('dlg_close') < nm.index('popup'))
    s,st=mk(clob,{PATH:real},track_has_file=False); run(s,'rev_item',dict(A4=0),0xc0780000)
    check('early error (no file): no dialog shown, none closed', not any(e[0] in ('show_msg','dlg_close') for e in st['log']))
print('FAILURES:', fails if fails else 'none')
