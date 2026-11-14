#!/usr/bin/env python3
"""Compare sim674's decoder with TI dis6x on every instruction of one or more .out files.
Usage: check_decode.py file.out ... ; exits non-zero on any mismatch."""
import sys, os, re, subprocess, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim674 as S

CGT = os.environ.get('CGT', '/Applications/ti/ti-cgt-c6000_8.5.0.LTS')
LINE = re.compile(r'^([0-9a-f]{8})\s+([0-9a-f]{8})\s+(\|\|)?\s*(?:\[\s*(!?)\s*([AB]\d+)\])?\s*([A-Z][A-Z0-9]*)(?:\.(\S+))?\s*(.*?)\s*$')
MEM = re.compile(r'^\*(\+\+|--|\+|-)?([AB]\d+)(\+\+|--)?(?:(\[)(.*)\]|(\()(.*)\))?$')

def num(t):
    t = t.strip()
    v = int(t, 16) if t.lower().startswith(('0x', '-0x')) else int(t)
    return v & 0xffffffff

def split_ops(t):
    out, depth, cur = [], 0, ''
    for ch in t:
        if ch in '[(': depth += 1
        if ch in '])': depth -= 1
        if ch == ',' and depth == 0: out.append(cur.strip()); cur = ''
        else: cur += ch
    if cur.strip(): out.append(cur.strip())
    return out

def norm_dis(op):
    m = re.search(r'\(PC[+-]\d+ = (0x[0-9a-f]+)\)', op)
    if m: return ('pc', int(m.group(1), 16))
    m = MEM.match(op)
    if m:
        pre, base, post = m.group(1) or '', m.group(2), m.group(3) or ''
        br = m.group(4) or m.group(6) or '['
        off = m.group(5) if m.group(4) else m.group(7)
        off = '0' if off is None else off
        off = off if re.match(r'^[AB]\d+$', off) else num(off)
        return ('mem', pre, base, post, br, off)
    if re.match(r'^[AB]\d+(:[AB]\d+)?$', op) or op.isupper() and not op.startswith('0X'): return op
    return num(op)

def norm_mine(o):
    if isinstance(o, int): return o & 0xffffffff
    return o

def check(path, show=200):
    dis = subprocess.run([f'{CGT}/bin/dis6x', path], capture_output=True, text=True).stdout.splitlines()
    import celf
    base, img, syms, top = celf.image(path)
    bad = collections.Counter(); n = 0; examples = {}
    data_addrs = set()
    for l in dis:
        m = LINE.match(l)
        if not m: continue
        addr, w = int(m.group(1), 16), int(m.group(2), 16)
        mn, unit, rest = m.group(6), m.group(7) or '', m.group(8)
        if mn in ('WORD',) or l.split()[2].startswith('.'): continue
        n += 1
        theirs = (mn, unit, (S.ridx(1 if m.group(5)[0] == 'B' else 0, int(m.group(5)[1:])), m.group(4) == '!') if m.group(5) else None,
                  [norm_dis(o) for o in split_ops(rest)])
        try:
            I = S.decode(w, addr)
            mine = (I.mn, I.unit, (I.pred[0], bool(I.pred[1])) if I.pred else None, [norm_mine(o) for o in I.ops])
        except S.Unsupported as e:
            mine = ('UNSUPPORTED', str(e))
        if mine != theirs:
            key = f'{mn}.{unit}'
            bad[key] += 1
            if key not in examples: examples[key] = (l.strip(), mine)
    for k, c in bad.most_common(show):
        print(f'{c:4d} {k:14s} dis6x: {examples[k][0]}\n{"":20s}mine:  {examples[k][1]}')
    print(f'{path}: {n} instructions, {sum(bad.values())} mismatches')
    return sum(bad.values())

if __name__ == '__main__':
    sys.exit(1 if sum(check(p) for p in sys.argv[1:]) else 0)
