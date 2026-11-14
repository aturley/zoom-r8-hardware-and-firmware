#!/usr/bin/env python3
"""hear.py rec.wav [freq ...]: per-window level and tone content (Goertzel) of a 16-bit stereo WAV, per channel."""
import sys, struct, math
def load(p):
    d = open(p, 'rb').read(); i = 12; ch = 2; sr = 48000
    while i + 8 <= len(d):
        cid, n = d[i:i+4], struct.unpack_from('<I', d, i+4)[0]
        if cid == b'fmt ': ch, sr = struct.unpack_from('<HI', d, i+10)
        if cid == b'data':
            a = struct.unpack_from('<%dh' % (n // 2), d, i+8)
            return sr, [[x / 32768 for x in a[c::ch]] for c in range(ch)]
        i += 8 + n + (n & 1)
def goertzel(x, f, sr):
    w = 2 * math.pi * f / sr; c = 2 * math.cos(w); s1 = s2 = 0.0
    for v in x: s1, s2 = v + c * s1 - s2, s1
    return 2 * math.sqrt(max(0.0, s1 * s1 + s2 * s2 - c * s1 * s2)) / len(x)     # sine amplitude
def db(a): return 20 * math.log10(a) if a > 1e-9 else -180
if __name__ == '__main__':
    sr, chans = load(sys.argv[1]); freqs = [float(f) for f in sys.argv[2:]] or [1000, 440]
    win = sr // 4
    print('time   ' + ''.join(f'| ch{c+1}: rms dBFS ' + ' '.join(f'{f:g}Hz' for f in freqs) + ' ' for c in range(len(chans))))
    for k in range(0, len(chans[0]) - win + 1, win):
        row = f'{k / sr:5.2f}s '
        for x in chans:
            seg = x[k:k + win]; rms = math.sqrt(sum(v * v for v in seg) / win)
            row += f'| {db(rms):7.1f}      ' + ' '.join(f'{db(goertzel(seg, f, sr)):6.1f}' for f in freqs) + '  '
        print(row)
