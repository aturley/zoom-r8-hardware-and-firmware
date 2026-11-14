import sys,subprocess,re; import os; _T=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,_T); os.environ.setdefault('R8_WORK',os.path.join(_T,'..','fw'))
import finfo
M=finfo.M
def lmvk(w):
    if (w>>2)&7==0b110 and (w>>5)&0x7f==0b0011010 and (w>>13)&0x1f==5:
        c=(w>>18)&0x1f; c=c-32 if c&16 else c
        return f'MVK(.L) {c},{"AB"[(w>>1)&1]}{(w>>23)&0x1f}'
names={'c0763c6c':'open','c0763b2c':'READ','c0764050':'SEEK','c0765c6c':'close','c0762db8':'write','c0762ee0':'fs_ee0'}
out=subprocess.run(['python3',os.path.join(_T,'disr.py'),sys.argv[1],sys.argv[2]],capture_output=True,text=True).stdout.splitlines()
for l in out:
    m=re.match(r'([0-9a-f]{8}) (.*)',l)
    if not m: print(l); continue
    a=int(m.group(1),16); t=m.group(2)
    if '<' in t:
        x=lmvk(M.u32(a))
        if x: t=x+' (32-bit .L)'
    for k,v in names.items(): t=t.replace('sub_'+k,v)
    print(f'{a:08x} {t}')
