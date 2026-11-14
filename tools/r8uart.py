#!/usr/bin/env python3
"""Zoom R8 panel-link log tools (pairs with the UART logger firmware).

  session  --out session.json       Guided capture: self-paced prompts (Enter to start / Enter when done).
  steps    --out steps.json         Write the default step list so you can edit it (then: session --steps steps.json).
  dump     UARTLOG.BIN              List messages (time, direction, bytes).
  analyze  UARTLOG.BIN session.json Correlate log traffic with the actions you performed; writes a Markdown report.
  synth    --out DIR                Make a synthetic log + session (for testing this script only).

Log file: 16-byte header ('R8UL', version 2, log start, byte count) then records:
  0xA5 | dir 'T' (DSP->panel) / 'R' (panel->DSP) | len (u16 LE) | TSC (u64 LE: TSCL, TSCH) | data[len]
TSC counts CPU cycles (default 360 MHz). The analysis aligns the log to the session using the start and end
marker actions (press STOP three times), so the exact clock rate does not need to be known.
"""
import argparse, json, struct, sys, time, statistics
from collections import defaultdict, Counter

# kind, instruction. 'idle' steps are timed pauses; every other step is self-paced (Enter to start, Enter when done).
DEFAULT_STEPS = [
    ('idle', 'Hands off the unit while the baseline is recorded.'),
    ('marker', 'START MARKER: press the STOP key three times, about half a second apart.'),
    ('idle', 'Hands off.'),
    ('sweep', 'Move fader 1 slowly all the way up, then slowly all the way back down.'),
    ('idle', 'Hands off.'),
    ('sweep', 'Move the MASTER fader slowly all the way up, then slowly all the way back down.'),
    ('idle', 'Hands off.'),
    ('press', 'Press and release the track 1 status key once (the lit key above fader 1).'),
    ('idle', 'Hands off.'),
    ('press', 'Tap pad 1 once softly; wait about a second; tap it once hard.'),
    ('idle', 'Hands off.'),
    ('press', 'If the unit has a rotary dial or encoder: turn it a few clicks one way, then back. Otherwise skip (s).'),
    ('idle', 'Hands off.'),
    ('screen', 'Change what the display shows: open any menu or settings screen, wait about two seconds, '
               'then go back to the screen you started from.'),
    ('idle', 'Hands off.'),
    ('marker', 'END MARKER: press the STOP key three times, about half a second apart.'),
    ('idle', 'Hands off.'),
]
IDLE_SECONDS = 8


# ---------------------------------------------------------------- parsing
def parse(path):
    d = open(path, 'rb').read()
    magic, ver, start, nbytes = struct.unpack_from('<4sIII', d, 0)
    if magic != b'R8UL':
        sys.exit('not a UARTLOG.BIN file')
    hl = 12 if ver >= 2 else 8
    body, recs, p, bad = d[16:16 + nbytes], [], 0, 0
    while p + hl <= len(body):
        if body[p] != 0xA5 or body[p + 1] not in (0x54, 0x52):
            p += 1; bad += 1; continue                        # resync after a torn record
        n = body[p + 2] | body[p + 3] << 8
        if p + hl + n > len(body):
            break
        ts = struct.unpack_from('<Q' if hl == 12 else '<I', body, p + 4)[0]
        recs.append((ts, chr(body[p + 1]), bytes(body[p + hl:p + hl + n])))
        p += hl + n
    return recs, bad


def messages(recs, hz, gap_s):
    """TX records are whole writes already. RX arrives byte by byte: join bytes closer than gap_s."""
    out = []
    cur = None
    for t, dr, b in recs:
        ts = t / hz
        if dr == 'T':
            out.append((ts, 'T', b))
            continue
        if cur and ts - cur[1] <= gap_s:
            cur[2].extend(b); cur[1] = ts
        else:
            if cur: out.append((cur[0], 'R', bytes(cur[2])))
            cur = [ts, ts, bytearray(b)]
    if cur: out.append((cur[0], 'R', bytes(cur[2])))
    out.sort(key=lambda m: m[0])
    return out


def key_of(m, prefix):
    return (m[1], m[2][:prefix].hex(), len(m[2]) if len(m[2]) < 16 else '16+')


def change_events(msgs, prefix):
    """Messages whose content differs from the previous message with the same key (repeats are dropped)."""
    last, ev = {}, []
    for m in msgs:
        k = key_of(m, prefix)
        if last.get(k) != m[2]:
            ev.append(m)
        last[k] = m[2]
    return ev


# ---------------------------------------------------------------- alignment
def find_markers(ev, min_hits=3, span=3.0, quiet=2.0):
    """Clusters of >=min_hits panel->DSP change events within `span` s, preceded by `quiet` s without any."""
    rx = [m[0] for m in ev if m[1] == 'R']
    found = []
    for i, t in enumerate(rx):
        if i and t - rx[i - 1] < quiet:
            continue
        j = i
        while j + 1 < len(rx) and rx[j + 1] - t <= span:
            j += 1
        if j - i + 1 >= min_hits:
            found.append((t, rx[j]))
    return found


def align(ev, steps):
    marks = [s for s in steps if s['kind'] == 'marker']
    cl = find_markers(ev)
    if marks and len(cl) == 1:
        l0, w0 = cl[0][0], marks[0]['t0']
        return (lambda w: l0 + (w - w0)), 'only the start marker was found; aligned on it with the nominal clock rate'
    if len(marks) < 2 or len(cl) < 2:
        return None, f'markers found in log: {len(cl)}; no alignment possible, using raw log time'
    (l0, _), (l1, _) = cl[0], cl[-1]
    w0, w1 = marks[0]['t0'], marks[-1]['t0']
    scale = (l1 - l0) / (w1 - w0) if w1 > w0 else 1.0
    return (lambda w: l0 + (w - w0) * scale), f'aligned on start/end markers; clock scale factor {scale:.4f}'


# ---------------------------------------------------------------- analysis
def spearman(xs, ys):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for k, i in enumerate(o): r[i] = k
        return r
    if len(xs) < 3: return 0.0
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def describe_bytes(ms, kind):
    """Which byte positions vary across these messages, their range, and (for sweeps) up/down trends."""
    if not ms: return ''
    L = min(len(m[2]) for m in ms)
    parts = []
    for i in range(min(L, 16)):
        vals = [m[2][i] for m in ms]
        if len(set(vals)) <= 1: continue
        desc = f'byte {i}: {min(vals)}..{max(vals)} ({len(set(vals))} values)'
        if kind == 'sweep' and len(vals) >= 6:
            h = len(vals) // 2
            up, down = spearman(list(range(h)), vals[:h]), spearman(list(range(len(vals) - h)), vals[h:])
            if abs(up) > 0.6 or abs(down) > 0.6:
                desc += f', trend first half {up:+.2f} / second half {down:+.2f}'
        parts.append(desc)
    if len(parts) > 4: parts = parts[:4] + [f'... {len(parts) - 4} more varying bytes']
    return '; '.join(parts) if parts else 'constant content'


def analyze(log, sess, hz, gap_s, prefix, margin):
    recs, bad = parse(log)
    steps = json.load(open(sess))['steps']
    msgs = messages(recs, hz, gap_s)
    ev = change_events(msgs, prefix)
    to_log, note = align(ev, steps)
    if to_log is None:
        to_log = lambda w: w
    idle = [(to_log(s['t0']) + margin, to_log(s['t1']) - margin) for s in steps if s['kind'] == 'idle']
    idle_dur = sum(max(0, b - a) for a, b in idle) or 1.0
    idle_cnt = Counter(key_of(m, prefix) for m in ev for a, b in idle if a <= m[0] <= b)
    out = ['# R8 panel-link correlation report', '',
           f'- Log: `{log}` — {len(recs)} records, {len(msgs)} messages, {len(ev)} content changes'
           + (f', {bad} bytes skipped while resyncing' if bad else ''),
           f'- Alignment: {note}',
           f'- Message key = direction + first {prefix} byte(s) + length. "Changes" counts messages whose content differs from the previous one with the same key.',
           '', '| Direction | Meaning |', '| --- | --- |', '| T | DSP to panel processor (display, LEDs) |', '| R | panel processor to DSP (keys, faders, pads) |', '']
    out += ['## Baseline (idle periods)', '',
            '| Key (dir, prefix, len) | Changes per second while idle |', '| --- | --- |']
    for k, c in idle_cnt.most_common(12):
        out.append(f'| {k[0]} {k[1]} len {k[2]} | {c / idle_dur:.2f} |')
    if not idle_cnt: out.append('| (none) | 0 |')
    for n, s in enumerate(steps):
        if s['kind'] in ('idle',): continue
        a, b = to_log(s['t0']) - margin, to_log(s['t1']) + margin
        win = [m for m in ev if a <= m[0] <= b]
        dur = max(b - a, 1e-6)
        cnt = Counter(key_of(m, prefix) for m in win)
        rows = []
        for k, c in cnt.most_common():
            rate, base = c / dur, idle_cnt.get(k, 0) / idle_dur
            if c >= 2 and rate > 3 * base + 0.2:
                ms = [m for m in win if key_of(m, prefix) == k]
                rows.append((k, c, rate, base, describe_bytes(ms, s['kind']), ms[:3]))
        out += ['', f'## Step {n + 1}: {s["text"]}', '']
        if not rows:
            out.append('No message type stood out above the idle baseline in this window.')
            continue
        out += ['| Key | Changes | Rate vs idle (/s) | What varies | First examples |', '| --- | --- | --- | --- | --- |']
        for k, c, rate, base, desc, ex in rows[:8]:
            exs = ' '.join(m[2][:12].hex() + ('…' if len(m[2]) > 12 else '') for m in ex)
            out.append(f'| {k[0]} {k[1]} len {k[2]} | {c} | {rate:.1f} vs {base:.2f} | {desc} | `{exs}` |')
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------- session capture
def run_session(out, steps=None, idle_s=IDLE_SECONDS):
    steps = steps or DEFAULT_STEPS
    print('Before starting: flash the logger firmware, power on normally and wait for the normal screen.')
    print('Each action is self-paced: read it, press Enter, do it, then press Enter again when finished.')
    print(f'"Hands off" pauses count down by themselves ({idle_s} s). Type s then Enter to skip an action.')
    input('\nPress Enter to begin... ')
    rec = []
    for i, (kind, text) in enumerate(steps):
        print(f'\n[{i + 1}/{len(steps)}] {text}')
        if kind == 'idle':
            t0 = time.time()
            while (left := idle_s - (time.time() - t0)) > 0:
                print(f'\r   hands off: {left:4.1f} s ', end='', flush=True); time.sleep(0.1)
            print('\r   hands off: done      ')
            rec.append({'kind': kind, 'text': text, 't0': t0, 't1': time.time()})
            continue
        if input('   Press Enter, then do it (s = skip): ').strip().lower() == 's':
            print('   skipped'); continue
        t0 = time.time()
        input('   ...press Enter when you have finished: ')
        rec.append({'kind': kind, 'text': text, 't0': t0, 't1': time.time()})
    print('\nDone. Now open the VERSION screen and wait for it to appear; then copy UARTLOG.BIN off the card.')
    t = rec[0]['t0']
    for r in rec: r['t0'] -= t; r['t1'] -= t
    json.dump({'steps': rec}, open(out, 'w'), indent=1)
    print(f'Session timing saved to {out}')


def load_steps(path):
    data = json.load(open(path))
    return [(d['kind'], d['text']) for d in data]


# ---------------------------------------------------------------- synthetic test data
def synth(outdir, hz=360e6, seed=1):
    """Fake protocol for testing the analysis only (it is NOT the R8's real protocol)."""
    import os, random
    random.seed(seed); os.makedirs(outdir, exist_ok=True)
    steps, t = [], 0.0
    for kind, text in DEFAULT_STEPS:
        dur = IDLE_SECONDS if kind == 'idle' else (12 if kind == 'sweep' else 5)
        steps.append({'kind': kind, 'text': text, 't0': t, 't1': t + dur}); t += dur
    lag, clk = 3.2, 1.003                                        # log starts 3.2 s earlier; clock 0.3% off
    recs = []
    def add(ts, d, b): recs.append((int(((ts + lag + random.uniform(0, 0.004)) * clk) * hz), d, b))
    def ev(ts, d, b): add(ts + 0.137, d, b)                     # keep action events off the 0.25 s status grid
    tt = 0.0
    while tt < t:                                                # periodic unchanged status + display refresh
        add(tt, 'R', b'\xf0\x00\x00'); add(tt + 0.01, 'T', b'\x80' + bytes(16)); tt += 0.25
    for n, s in enumerate(steps):
        a, b = s['t0'], s['t1']
        if s['kind'] == 'marker':
            for k in range(3):
                ev(a + 0.6 + 0.5 * k, 'R', b'\x90\x05\x01'); ev(a + 0.75 + 0.5 * k, 'R', b'\x90\x05\x00')
        if s['kind'] == 'sweep':
            ch = 1 if 'fader 1' in s['text'] else 9
            a += 0.5
            for k in range(80):
                v = int(127 * (k / 40 if k < 40 else (80 - k) / 40))
                ev(a + 1 + k * 0.1, 'R', bytes([0xb0, ch, v]))
        if s['kind'] == 'press' and 'track 1' in s['text']:
            ev(a + 1, 'R', b'\x90\x11\x01'); ev(a + 1.3, 'R', b'\x90\x11\x00'); ev(a + 1.32, 'T', b'\x81\x11\x01')
        if s['kind'] == 'press' and 'pad' in s['text']:
            ev(a + 1, 'R', b'\xa0\x01\x20'); ev(a + 2.5, 'R', b'\xa0\x01\x7c')
        if s['kind'] == 'screen':
            for k in range(8): ev(a + 1 + k * 0.05, 'T', bytes([0x82, k]) + bytes(random.randrange(256) for _ in range(128)))
    recs.sort(key=lambda r: r[0])
    body = b''.join(struct.pack('<BBHQ', 0xA5, ord(d), len(b), ts) + b for ts, d, b in recs)
    open(f'{outdir}/UARTLOG.BIN', 'wb').write(struct.pack('<4sIII', b'R8UL', 2, 0xc0050000, len(body)) + body)
    json.dump({'steps': steps}, open(f'{outdir}/session.json', 'w'), indent=1)
    print(f'wrote {outdir}/UARTLOG.BIN ({len(recs)} records) and {outdir}/session.json')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('session'); s.add_argument('--out', default='session.json')
    s.add_argument('--steps', help='JSON list of {"kind","text"} steps (kinds: idle, marker, sweep, press, screen)')
    s.add_argument('--idle', type=float, default=IDLE_SECONDS, help='seconds for each hands-off pause')
    w = sub.add_parser('steps'); w.add_argument('--out', default='steps.json')
    d = sub.add_parser('dump'); d.add_argument('log'); d.add_argument('--mhz', type=float, default=360); d.add_argument('--gap-ms', type=float, default=0.3)
    a = sub.add_parser('analyze'); a.add_argument('log'); a.add_argument('session')
    a.add_argument('--mhz', type=float, default=360); a.add_argument('--gap-ms', type=float, default=0.3)
    a.add_argument('--prefix', type=int, default=1, help='bytes of each message used to group message types')
    a.add_argument('--margin', type=float, default=0.5); a.add_argument('--report', default='report.md')
    y = sub.add_parser('synth'); y.add_argument('--out', default='synthetic')
    o = ap.parse_args()
    if o.cmd == 'session': run_session(o.out, load_steps(o.steps) if o.steps else None, o.idle)
    elif o.cmd == 'steps':
        json.dump([{'kind': k, 'text': t} for k, t in DEFAULT_STEPS], open(o.out, 'w'), indent=1)
        print(f'wrote {o.out}; edit the texts or remove steps, then run: session --steps {o.out}')
    elif o.cmd == 'synth': synth(o.out)
    elif o.cmd == 'dump':
        recs, bad = parse(o.log)
        for ts, dr, b in messages(recs, o.mhz * 1e6, o.gap_ms / 1000):
            print(f'{ts:12.6f} {dr} {b.hex(" ")}')
    elif o.cmd == 'analyze':
        rep = analyze(o.log, o.session, o.mhz * 1e6, o.gap_ms / 1000, o.prefix, o.margin)
        open(o.report, 'w').write(rep); print(rep)


if __name__ == '__main__':
    main()
