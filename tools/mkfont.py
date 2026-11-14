#!/usr/bin/env python3
"""Turn the font drawing (cbuild/forth/font.txt) into the C header the apps include (cbuild/forth/font.h).

    python3 tools/mkfont.py cbuild/forth/font.txt cbuild/forth/font.h
    python3 tools/mkfont.py --stock fw/R8SYS.BIN cbuild/forth/font_stock.h     (opt-in, see below)

font.txt holds the 95 printable ASCII glyphs, 0x20 to 0x7E in order. Each is a line starting with its code
(0x41 A), then 7 rows of 5 columns, '#' lit and '.' dark. Lines starting with # are comments.
font.h gets FONT6[95][6]: six bytes per glyph, one per column (the sixth always 0, the gap between characters),
bit 0 = the top row, the LCD's page format. The font is our own design; nothing here comes from the stock firmware.

--stock copies the stock R8 font out of your own R8SYS.BIN (1.17) instead, in the same format. The build scripts do
this when run with FONT=stock (cbuild/font.sh), and font.h then includes font_stock.h. A binary built that way
contains Zoom's font data, so keep it for your own use. This repository contains no stock font data.
Standard library only."""
import sys


def parse(text):
    glyphs, cur = [], None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('# '):
            continue
        if s.startswith('0x'):
            code = int(s.split()[0], 16)
            if code != 0x20 + len(glyphs):
                sys.exit(f'line {n}: expected 0x{0x20 + len(glyphs):02X}, found 0x{code:02X}')
            cur = []; glyphs.append(cur)
            continue
        if cur is None or len(s) != 5 or set(s) - set('.#') or len(cur) == 7:
            sys.exit(f'line {n}: expected a row of five . or # characters: {line!r}')
        cur.append(s)
    if len(glyphs) != 95 or any(len(g) != 7 for g in glyphs):
        sys.exit(f'expected 95 glyphs of 7 rows, found {len(glyphs)}')
    return glyphs


def columns(rows):
    return [sum(1 << y for y in range(7) if rows[y][x] == '#') for x in range(5)] + [0]


STOCK_SHA = '6e4bf64e'                   # sha256 prefix of stock R8SYS.BIN 1.17
STOCK_ZERO = 0xF7618                     # file offset of the glyph for '0' in 1.17


def stock(fw, out):
    import hashlib
    d = open(fw, 'rb').read()
    if not hashlib.sha256(d).hexdigest().startswith(STOCK_SHA):
        sys.exit(f'{fw}: not stock R8SYS.BIN 1.17 (the font offset is only known for 1.17)')
    base = STOCK_ZERO - 16 * 6                                   # the table starts at the space, 16 glyphs before '0'
    if d[base:base + 6] != bytes(6) or any(d[base + 6 * i + 5] for i in range(95)):
        sys.exit(f'{fw}: no font table where expected')
    with open(out, 'w') as f:
        f.write('/* The stock R8 font, copied from your own R8SYS.BIN by tools/mkfont.py --stock (FONT=stock builds).\n'
                '   Zoom\'s data: not part of this repository, and binaries built with it are for your own use. */\n')
        f.write('static const unsigned char FONT6[95][6] = {\n')
        for i in range(95):
            f.write('    {' + ','.join(f'0x{b:02X}' for b in d[base + 6 * i:base + 6 * i + 6]) + '},\n')
        f.write('};\n')


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--stock':
        return stock(sys.argv[2], sys.argv[3])
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    glyphs = parse(open(sys.argv[1]).read())
    cols = [columns(g) for g in glyphs]
    dup = {}
    for i, c in enumerate(cols):
        dup.setdefault(tuple(c), []).append(chr(0x20 + i))
    clash = [v for v in dup.values() if len(v) > 1]
    if clash:                       # tests read the screen back by glyph, so every glyph must be unique
        sys.exit(f'glyphs that look identical: {clash}')
    with open(sys.argv[2], 'w') as f:
        f.write('/* 5x7 font, ASCII 0x20..0x7E, 6 columns each, bit 0 = top. Generated from font.txt by\n'
                '   tools/mkfont.py; edit font.txt, not this file. Our own design (MIT, see LICENSE). */\n')
        f.write('#ifdef R8_STOCK_FONT                /* FONT=stock builds (cbuild/font.sh): your own copy of the stock font */\n'
                '#include "font_stock.h"\n'
                '#else\n')
        f.write('static const unsigned char FONT6[95][6] = {\n')
        for i, c in enumerate(cols):
            ch = chr(0x20 + i)
            note = 'space' if ch == ' ' else ('backslash' if ch == '\\' else ch)
            f.write('    {' + ','.join(f'0x{b:02X}' for b in c) + f'}},  /* 0x{0x20 + i:02X} {note} */\n')
        f.write('};\n#endif\n')


if __name__ == '__main__':
    main()
