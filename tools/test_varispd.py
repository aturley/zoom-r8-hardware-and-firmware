#!/usr/bin/env python3
"""VARISPD.BIN (cbuild/utils: varispd.c + browse.c) in sim674 with the stock functions mocked (r8mock.py) and a
scripted menu engine: it ENTERs rows (stores the row in the first state word, calls the record's +16 handler, as
0xC078F3F8) and turns the wheel on rows as the stock engine does (0xC078EFE4: skip rows with record +24 bit 3;
add the movement to *record[+28], clamp to record +36..+40 (wrap if +24 bit 4), write back if changed, call the
record's +44 hook). Checks the SPEED / LENGTH menu (wheel steps of 0.1x, -4.0x..+4.0x, 0 skipped, LENGTH follows),
the new file's name, header and length, and the audio: a sine comes out at the new pitch and the same level
(least-squares fit, residual = everything else), negative speeds play backwards, speeding up to 4x does not alias,
stereo channels stay apart, 24-bit and float formats are kept, the source is untouched, a non-WAV is refused."""
import os, sys, struct, math
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock, mkutil
# the stock firmware's strings the utility passes to dialogs (stand-ins at their addresses)
STR = {0xc06e77dd: 'Please Wait...', 0xc06e7960: 'ACCESS ERROR', 0xc06e785d: 'Invalid File'}

def nested(sim, fn, a4=None):
    """call fn inside the running simulation (as the menu engine calls handlers) and return its A4"""
    R = list(sim.R); pend, br = sim.pend, sim.branches
    sim.pend, sim.branches = [], []
    sim.R[S.ridx(1, 15)] = (R[S.ridx(1, 15)] - 0x800) & ~7
    if a4 is not None: sim.R[4] = a4
    stop = 0xcafe1000 + 16 * len(R) % 0x100
    sim.R[S.ridx(1, 3)] = stop
    sim.run(fn, stop, 3_000_000_000)
    ret = sim.R[4]
    sim.R[:] = R; sim.pend, sim.branches = pend, br
    return ret
W = os.path.join(HERE, '..')
img, base, entry, top = mkutil.pack(os.path.join(W, 'cbuild', 'utils', 'out', 'varispd.out'))
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

PCTS = [-50, -33, -25, -20, -15, -10, -5, -2, -1, 1, 2, 5, 10, 15, 20, 25, 33, 50, 100]
def wav(rate, bits, chans, frames, fmt=1):
    """chans: one function per channel, sample index -> value in [-1, 1)"""
    out = bytearray()
    for i in range(frames):
        for f in chans:
            v = f(i)
            if fmt == 3: out += struct.pack('<f', v)
            elif bits == 16: out += struct.pack('<h', max(-32768, min(32767, round(v * 32768))))
            elif bits == 24: out += struct.pack('<i', max(-8388608, min(8388607, round(v * 8388608))))[:3]
    ba = len(chans) * bits // 8
    f = struct.pack('<HHIIHH', fmt, len(chans), rate, rate * ba, ba, bits)
    return b'RIFF' + struct.pack('<I', 36 + len(out)) + b'WAVEfmt ' + struct.pack('<I', 16) + f + b'data' + struct.pack('<I', len(out)) + bytes(out)
def parse(b):
    assert b[:4] == b'RIFF' and b[8:16] == b'WAVEfmt '
    fmt, ch, rate, br, ba, bits = struct.unpack_from('<HHIIHH', b, 20)
    assert b[36:40] == b'data'
    n = struct.unpack_from('<I', b, 40)[0]; d = b[44:44 + n]
    ss = []
    for k in range(0, len(d), ba):
        fr = []
        for c in range(ch):
            o = k + c * bits // 8
            if fmt == 3: fr.append(struct.unpack_from('<f', d, o)[0])
            elif bits == 16: fr.append(struct.unpack_from('<h', d, o)[0] / 32768)
            else: fr.append(int.from_bytes(d[o:o + 3], 'little', signed=True) / 8388608)
        ss.append(fr)
    return dict(fmt=fmt, ch=ch, rate=rate, br=br, ba=ba, bits=bits, n=n, riff=struct.unpack_from('<I', b, 4)[0], frames=ss)
def fit(y, f, rate):
    """least-squares a cos + b sin at f; returns (amplitude, residual rms)"""
    w = 2 * math.pi * f / rate
    c = [math.cos(w * i) for i in range(len(y))]; s = [math.sin(w * i) for i in range(len(y))]
    cc = sum(x * x for x in c); ssq = sum(x * x for x in s); cs = sum(a * b for a, b in zip(c, s))
    yc = sum(a * b for a, b in zip(y, c)); ys = sum(a * b for a, b in zip(y, s))
    det = cc * ssq - cs * cs; a = (yc * ssq - ys * cs) / det; b = (ys * cc - yc * cs) / det
    res = math.sqrt(sum((v - a * x - b * z) ** 2 for v, x, z in zip(y, c, s)) / len(y))
    return math.hypot(a, b), res
db = lambda x: 20 * math.log10(max(x, 1e-12))

def run(files, plan, lfns=None, short_only=False):
    """plan: one list of actions per menu opened: ('enter', row), ('wheel', row, movement), 'snap' (record the
    rows' values)"""
    sim = S.Sim(); sim.write(base, img[32:])
    for a, t in STR.items(): sim.write(a, t.encode() + b'\0')
    env = r8mock.R8Env(sim, files)
    env.lfns = lfns or {}; env.short_names_only = short_only
    seen = []
    rd = lambda a, n: struct.unpack('<%dI' % n, sim.read(a, 4 * n))
    sgn = lambda v: v - (1 << 32) if v >> 31 else v
    def menu(s):
        desc = s.arg(0)
        def values():
            d = rd(desc, 12); out = []
            for r in rd(d[8], d[0]):
                rec = rd(r, 20)
                if rec[8]:
                    sim.write(0xC0080000, struct.pack('<4I', 0, 0, 0, rec[7]))
                    out.append(s.cstr(nested(sim, rec[8], 0xC0080000)))
                else: out.append(None)
            return out
        d = rd(desc, 12)
        labels = [s.cstr(rd(r, 1)[0]) for r in rd(d[8], d[0])]
        entry = dict(title=s.cstr(d[1]), labels=labels, values=values(), start=rd(d[5], 1)[0], snaps=[],
                     recs=[rd(r, 20) for r in rd(d[8], d[0])])
        seen.append(entry)
        for act in (plan.pop(0) if plan else []):
            d = rd(desc, 12); recs = rd(d[8], d[0])
            if act == 'snap': entry['snaps'].append(values()); continue
            if act[0] == 'enter':
                sim.write(d[5], struct.pack('<I', act[1]))
                nested(sim, rd(recs[act[1]] + 16, 1)[0])
            else:
                _, row, mv = act
                rec = rd(recs[row], 20)
                if rec[6] & 8: continue
                old = sgn(rd(rec[7], 1)[0]); new = old + mv; lo, hi = sgn(rec[9]), sgn(rec[10])
                if rec[6] & 0x10: new = hi if new < lo else lo if new > hi else new
                else: new = max(lo, min(hi, new))
                if new == old and not rec[6] & 0x20: continue
                sim.wr32(rec[7], new)
                if rec[11]:
                    sim.write(0xC0080100, struct.pack('<5I', 0x10000000, row, 0, rec[7], mv & 0xffffffff))
                    nested(sim, rec[11], 0xC0080100)
        d = rd(desc, 12)                           # the menu as it stands when it is EXITed
        entry['after'] = values()
        s.R[4] = 0
    sim.mocks[r8mock.ADDR['r8_menu_engine']] = menu
    rc = sim.call(entry, (0, 0))
    pops = [x[1] for x in env.log if x[0] == 'popup']
    return rc, seen, env, pops

N = 1500
R = 44100
src = wav(R, 16, [lambda i: 0.5 * math.sin(2 * math.pi * 1000 * i / R)], N)
files = {'A:\\TRACK01.WAV': src, 'A:\\NOTES.TXT': b'not a wav'}
E = lambda r: ('enter', r)
Wh = lambda mv: ('wheel', 0, mv)
mid = lambda fr, c=0: [x[c] for x in fr[100:-100]]

# root: TRACK01.WAV is row 1; then SPEED +1.0 -> +2.0 (wheel +10), ENTER; -> +0.5 (wheel -15), ENTER; EXIT
rc, seen, env, pops = run(files, [[E(1)], ['snap', Wh(10), 'snap', E(0), Wh(-15), 'snap', E(1)]])
sm = seen[1]
print('  speed menu:', sm['labels'], sm['values'], 'start', sm['start'], 'snaps', sm['snaps'])
check('speed menu: titled with the file; SPEED +1.0x and LENGTH, cursor on SPEED; the LENGTH row is read-only for the wheel',
      sm['title'] == 'TRACK01.WAV' and sm['labels'] == ['SPEED', 'LENGTH'] and sm['values'] == ['+1.0x', '0:00.03']
      and sm['start'] == 0 and sm['recs'][1][6] & 8 and not sm['recs'][0][6] & 8)
check('the wheel sets SPEED in tenths and LENGTH follows: +2.0x (half as long), +0.5x (twice)',
      sm['snaps'] == [['+1.0x', '0:00.03'], ['+2.0x', '0:00.01'], ['+0.5x', '0:00.06']])
check('ENTER (on either row) makes "TRACK01 +2.0x.WAV" and "TRACK01 +0.5x.WAV"; source untouched; popups SAVED; files closed',
      pops == ['SAVED', 'SAVED'] and env.fs['A:\\TRACK01.WAV'] == src and not env.fds
      and {'A:\\TRACK01 +2.0x.WAV', 'A:\\TRACK01 +0.5x.WAV'} <= set(env.fs))
check('the folder list is refreshed after the action: the new files are in it before EXIT',
      seen[0]['values'] == ['NOTES.TXT', 'TRACK01.WAV'] and seen[0]['after'] == ['NOTES.TXT', 'TRACK01 +0.5x.WAV', 'TRACK01 +2.0x.WAV', 'TRACK01.WAV'])
up, dn = parse(bytes(env.fs['A:\\TRACK01 +2.0x.WAV'])), parse(bytes(env.fs['A:\\TRACK01 +0.5x.WAV']))
check('+2.0x: header (16-bit mono 44100), %d frames = ceil(%d / 2), RIFF size' % (len(up['frames']), N),
      (up['fmt'], up['ch'], up['rate'], up['bits'], up['ba'], up['br']) == (1, 1, R, 16, 2, 2 * R)
      and len(up['frames']) == (N + 1) // 2 and up['riff'] == 36 + up['n'])
check('+0.5x: %d frames = 2 x %d' % (len(dn['frames']), N), len(dn['frames']) == 2 * N)
a, res = fit(mid(up['frames']), 2000, R)
print('  +2.0x: 2 kHz at %.3f dB re input, residual %.1f dB' % (db(a / 0.5), db(res / 0.5)))
check('+2.0x: 1 kHz becomes 2 kHz, level within 0.05 dB, residual below -80 dB (16-bit dither)', abs(db(a / 0.5)) < 0.05 and db(res / 0.5) < -80)
a, res = fit(mid(dn['frames']), 500, R)
print('  +0.5x: 500 Hz at %.3f dB re input, residual %.1f dB' % (db(a / 0.5), db(res / 0.5)))
check('+0.5x: 1 kHz becomes 500 Hz, level within 0.05 dB, residual below -80 dB', abs(db(a / 0.5)) < 0.05 and db(res / 0.5) < -80)

# the wheel: limits and skipping 0 (no ENTER, so nothing is written)
rc, seen, env, pops = run(files, [[E(1)], [Wh(100), 'snap', Wh(-39), 'snap', Wh(-1), 'snap', Wh(+1), 'snap', Wh(-3), 'snap',
                                           Wh(-100), 'snap', ('wheel', 1, 5), 'snap']])
sp = [v[0] for v in seen[1]['snaps']]
print('  wheel:', sp)
check('the wheel stops at +4.0x and -4.0x, skips 0 both ways; the wheel on LENGTH does nothing',
      sp == ['+4.0x', '+0.1x', '-0.1x', '+0.1x', '-0.2x', '-4.0x', '-4.0x'] and pops == [])

# backwards: a 1 kHz sine whose level rises from 0.1 to 0.6; at -1.0x it falls, at -2.0x it falls and is 2 kHz
ramp = wav(R, 24, [lambda i: (0.1 + 0.5 * i / 1200) * math.sin(2 * math.pi * 1000 * i / R)], 1200)
rc, seen, env, pops = run({'A:\\RAMP.WAV': ramp}, [[E(0)], [Wh(-20), E(0), Wh(-10), E(0)]])
r1, r2 = parse(bytes(env.fs['A:\\RAMP -1.0x.WAV'])), parse(bytes(env.fs['A:\\RAMP -2.0x.WAV']))
src_fr = parse(ramp)['frames']
err = max(abs(a[0] - b[0]) for a, b in zip(r1['frames'][100:-100], list(reversed(src_fr))[100:-100]))
env2 = lambda fr: (math.sqrt(sum(v * v for v in fr[:len(fr) // 4]) / (len(fr) // 4)), math.sqrt(sum(v * v for v in fr[-len(fr) // 4:]) / (len(fr) // 4)))
e1, e2 = env2(mid(r2['frames']))
a2, _ = fit(mid(r2['frames']), 2000, R)
print('  -1.0x: max difference from the reversed source %.2e; -2.0x: first/last quarter rms %.3f / %.3f, 2 kHz amplitude %.3f' % (err, e1, e2, a2))
check('-1.0x is the source backwards (within 0.001, the filter only), same length', len(r1['frames']) == 1200 and err < 1e-3)
check('-2.0x: backwards (falling level), half as long, 1 kHz becomes 2 kHz', len(r2['frames']) == 600 and e1 > 2 * e2 and a2 > 0.2)

# +4.0x: 1 kHz -> 4 kHz; 6 kHz -> 24 kHz (above Nyquist) is filtered out
hi = wav(R, 24, [lambda i: 0.25 * math.sin(2 * math.pi * 1000 * i / R) + 0.25 * math.sin(2 * math.pi * 6000 * i / R)], 2400)
rc, seen, env, pops = run({'A:\\HI.WAV': hi}, [[E(0)], [Wh(30), E(0)]])
o = parse(bytes(env.fs['A:\\HI +4.0x.WAV']))
a, res = fit(mid(o['frames'][:]), 4000, R)
print('  +4.0x: 4 kHz at %.3f dB, everything else (incl. the 24 kHz that would alias to 20.1 kHz) %.1f dB' % (db(a / 0.25), db(res / 0.25)))
check('+4.0x (24-bit): 1 kHz becomes 4 kHz at the same level, the 6 kHz part (24 kHz) is filtered out (below -70 dB)',
      len(o['frames']) == 600 and abs(db(a / 0.25)) < 0.05 and db(res / 0.25) < -70)

# 24-bit stereo at +1.1x: left 1 kHz -> 1.1 kHz, right 3 kHz -> 3.3 kHz, no crosstalk
st = wav(R, 24, [lambda i: 0.4 * math.sin(2 * math.pi * 1000 * i / R), lambda i: 0.3 * math.sin(2 * math.pi * 3000 * i / R)], 1200)
rc, seen, env, pops = run({'A:\\ST.WAV': st}, [[E(0)], [Wh(1), E(0)]])
o = parse(bytes(env.fs['A:\\ST +1.1x.WAV']))
aL, rL = fit(mid(o['frames'], 0), 1100, R); aR, rR = fit(mid(o['frames'], 1), 3300, R)
print('  +1.1x stereo: L %.3f dB res %.1f, R %.3f dB res %.1f; %d frames' % (db(aL / 0.4), db(rL / 0.4), db(aR / 0.3), db(rR / 0.3), len(o['frames'])))
check('+1.1x, 24-bit stereo: channels kept apart at 1.1 and 3.3 kHz, levels within 0.05 dB, residual below -90 dB (the filter\'s stopband)',
      (o['ch'], o['bits'], o['ba']) == (2, 24, 6) and len(o['frames']) == math.ceil(1200 * 10 / 11)
      and max(abs(db(aL / 0.4)), abs(db(aR / 0.3))) < 0.05 and max(db(rL / 0.4), db(rR / 0.3)) < -90)

# 32-bit float stays float
fl = wav(R, 32, [lambda i: 0.25 * math.sin(2 * math.pi * 440 * i / R)], 1000, fmt=3)
rc, seen, env, pops = run({'A:\\FL.WAV': fl}, [[E(0)], [Wh(-1), E(0)]])
o = parse(bytes(env.fs['A:\\FL +0.9x.WAV']))
a, res = fit(mid(o['frames']), 440 * 0.9, R)
print('  +0.9x float: %.4f dB res %.1f dB' % (db(a / 0.25), db(res / 0.25)))
check('+0.9x, 32-bit float: format 3 kept, 440 Hz -> 396 Hz at the same level, residual below -90 dB', o['fmt'] == 3 and o['bits'] == 32
      and abs(db(a / 0.25)) < 0.05 and db(res / 0.25) < -90)

# naming (short files: these runs are about names, not audio)
tiny = wav(R, 16, [lambda i: 0.1 * math.sin(i / 5)], 60)
rc, seen, env, pops = run({'A:\\BIKE_~18.WAV': tiny}, [[E(0)], [Wh(5), E(0), E(0)]], lfns={'A:\\BIKE_~18.WAV': 'Bike loop 120.wav'})
check('the long name is used, with its own extension case; the same speed again gets " (2)"',
      {'A:\\Bike loop 120 +1.5x.wav', 'A:\\Bike loop 120 +1.5x (2).wav'} <= set(env.fs) and pops == ['SAVED', 'SAVED'])
rc, seen, env, pops = run({'A:\\TRACK01.WAV': tiny}, [[E(0)], [Wh(10), E(0), E(0)]], short_only=True)
check('if the long name cannot be created: 8.3 fallback TRACK01V.WAV, then TRACK0V2.WAV, and the popup names it',
      {'A:\\TRACK01V.WAV', 'A:\\TRACK0V2.WAV'} <= set(env.fs) and pops == ['SAVED TRACK01V.WAV', 'SAVED TRACK0V2.WAV'])
rc, seen, env, pops = run(files, [[E(0)]])
check('a file that is not a WAV: "Invalid File" popup, no speed menu, nothing written',
      len(seen) == 1 and pops == ['Invalid File'] and set(env.fs) == set(files))
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
