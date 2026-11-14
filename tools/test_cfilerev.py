#!/usr/bin/env python3
"""C version of REVERSE's file work (cbuild/simtests/t_filerev.c) in sim674 on the real R8 recording,
with the firmware file API mocked. Output must equal the Python reference byte for byte."""
import os, sys, struct, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sim674 as S, r8mock
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n)
    if not c: fails.append(n)

def expected(w):
    p, ba = 12, 0
    while True:
        cid, n = w[p:p + 4], struct.unpack_from('<I', w, p + 4)[0]
        if cid == b'fmt ': ba = struct.unpack_from('<H', w, p + 20)[0]
        if cid == b'data': doff, dlen = p + 8, n; break
        p += 8 + n + (n & 1)
    fr = [w[doff + i:doff + i + ba] for i in range(0, dlen, ba)]
    return w[:doff] + b''.join(reversed(fr))

def run(files):
    sim = S.Sim(); base, img, syms, top = S.load_elf(sim, os.path.join(HERE, '..', 'cbuild', 'out', 't_filerev.out'))
    env = r8mock.R8Env(sim, files)
    t = time.time(); rc = sim.call(syms['r8_entry'], (0, 0))
    return rc, env, sim, time.time() - t

real = open(os.path.join(HERE, '..', 'data', 'REVIN_R8.WAV'), 'rb').read()
rc, env, sim, dt = run({'A:\\IN.WAV': real})
print(f'  R8 recording: {len(real)} bytes, {sim.ninstr:,} instructions, {len(env.log)} firmware calls, {dt:.1f}s')
check('R8 recording (384 KB LIST chunk, stereo 16-bit): status 0, byte-exact reversal', rc == 0 and bytes(env.fs['A:\\OUT.WAV']) == expected(real))
check('all files closed', not env.fds)
mono = (b'RIFF' + struct.pack('<I', 36 + 6000) + b'WAVE' + b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, 44100, 132300, 3, 24)
        + b'data' + struct.pack('<I', 6000) + bytes(range(256)) * 23 + bytes(112))
rc, env, sim, dt = run({'A:\\IN.WAV': mono})
check('24-bit mono, 3-byte frames: byte-exact', rc == 0 and bytes(env.fs['A:\\OUT.WAV']) == expected(mono))
rc, env, sim, dt = run({})
check('missing input: status 10, no output', rc == 10 and 'A:\\OUT.WAV' not in env.fs)
rc, env, sim, dt = run({'A:\\IN.WAV': b'RIFF\0\0\0\0JUNK'})
check('not a WAVE file: status 11, input closed', rc == 11 and not env.fds)
print('FAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
