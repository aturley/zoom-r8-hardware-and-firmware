#!/usr/bin/env python3
"""Generate cbuild/forth/fadergain.h: the R8 fader law (stock 1.17 breakpoints), as linear gains for 0..127."""
BP = [(1, -60.0), (6, -40.0), (14, -30.0), (35, -20.0), (59, -10.0), (100, 0.0), (127, 12.0)]
def db(v):
    if v == 0: return None
    for (a, da), (b, dbb) in zip(BP, BP[1:]):
        if a <= v <= b: return da + (dbb - da) * (v - a) / (b - a)
def gain(v): return 0.0 if v == 0 else 10 ** (db(v) / 20)
if __name__ == '__main__':
    print([round(gain(v), 6) for v in range(128)])
