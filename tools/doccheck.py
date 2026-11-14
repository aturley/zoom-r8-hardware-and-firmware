#!/usr/bin/env python3
"""doccheck.py: check the writeup and READMEs for the mistakes that creep in as the project changes.

    python3 tools/doccheck.py            report problems as file:line: message; exit 1 if any
    python3 tools/doccheck.py --stale    also list the stale-phrase rules

Checks:
  links     relative Markdown links point at files that exist, and #anchors at headings that exist
  paths     repository paths in backticks (cbuild/..., tools/..., writeup/..., or a tools/*.py name) exist
  private   no references to material that isn't published: the private notes, the work log, local folders,
            personal paths
  draft     no leftover placeholders (@NAME@, TODO, TBD, FIXME, XXX) and no "first draft" outside the README's
            status column
  stale     phrases that were true once and are now wrong (STALE below). When you change a fact, add the old
            wording here so it can't come back.
Standard library only.
"""
import glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STALE = [   # (regex, what is true now)
    (r'\b39 key IDs\b|\b39th key\b|38 of the R8.s 39 keys', '38 keys and the REPEAT/STOP pad on the panel link, ID 86 unknown, TEMPO on GPIO'),
    (r'stock SD driver uses no DMA', 'stock moves the bulk of each SD transfer by DMA (section 6)'),
    (r'forth/looper\.c|forth/vectors\.asm', 'the looper is in cbuild/sampler/'),
    (r'tools/bp_host', 'bp_host.c is in cbuild/blippoo/'),
    (r'card clock stays at 18 MHz|Ours runs at 18 MHz', 'the card clock is 22.5 MHz (CLKRT 3) since 14 Nov'),
    (r'(?i)keep (multi-block )?transfers (to|at) (7|seven) blocks', 'the driver uses DMATRIG and runs of up to 128 blocks'),
    (r'(?i)license is (still )?to be determined', 'MIT, see LICENSE'),
    (r'(?i)apps take the LCD font from', 'the apps use our own font (cbuild/forth/font.txt); FONT=stock is opt-in'),
    (r'R8 Forth - WORDS', 'the banner is built from the app title'),
]
PRIVATE = [
    (r'r8-workspace', 'a local folder outside the repository'),
    (r'scratchpad', 'a local scratch folder'),
    (r'(?i)\bwork ?log\b', 'the private work log'),
    (r'(?i)research[- ]questions?|\bQ\d{1,3}\b', 'the private research questions'),
    (r'/Users/|/home/[a-z]', 'a personal path'),
    (r'[\w.+-]+@[\w-]+\.(com|org|net)\b', 'an email address'),
]
DRAFT = [(r'@[A-Z_]{3,}@', 'placeholder'), (r'\b(TODO|TBD|FIXME|XXX)\b', 'to-do marker')]


def slug(h):
    s = re.sub(r'[^\w\- ]', '', h.strip().lower())
    return s.replace(' ', '-')


def anchors(path):
    try:
        return {slug(m.group(1)) for m in re.finditer(r'^#+\s+(.*)$', open(path).read(), re.M)}
    except OSError:
        return set()


def main():
    show_stale = '--stale' in sys.argv
    if show_stale:
        for rx, now in STALE:
            print(f'stale rule: /{rx}/ -> {now}')
    files = sorted(glob.glob(os.path.join(ROOT, 'writeup', '*.md'))) + [os.path.join(ROOT, 'README.md')] + \
            sorted(glob.glob(os.path.join(ROOT, '.claude', 'skills', '*', 'SKILL.md')))
    tools = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, 'tools', '*'))}
    problems = []
    for f in files:
        rel = os.path.relpath(f, ROOT)
        text = open(f).read()
        in_code = False
        for n, line in enumerate(text.splitlines(), 1):
            if line.startswith('```'):
                in_code = not in_code
            def bad(msg):
                problems.append(f'{rel}:{n}: {msg}')
            # links
            for m in re.finditer(r'\]\(([^)\s]+)\)', re.sub(r'`[^`]*`', '', line)):   # not inside code spans
                t = m.group(1)
                if t.startswith(('http://', 'https://', 'mailto:')):
                    continue
                target, _, frag = t.partition('#')
                tp = os.path.normpath(os.path.join(os.path.dirname(f), target)) if target else f
                if target and not os.path.exists(tp):
                    bad(f'link to a missing file: {t}')
                elif frag and tp.endswith('.md') and frag not in anchors(tp):
                    bad(f'link to a missing heading: {t}')
            # paths in backticks
            for m in re.finditer(r'`([^`\s]+)`', line):
                p = m.group(1).rstrip('.,;:')
                if re.match(r'^(cbuild|tools|writeup)/', p) and not any(c in p for c in '*<>{}$'):
                    if not os.path.exists(os.path.join(ROOT, p)):
                        bad(f'path does not exist: {p}')
                elif re.fullmatch(r'[\w-]+\.py', p) and p not in tools and not glob.glob(os.path.join(ROOT, '**', p), recursive=True):
                    bad(f'no such script: {p}')
            # private material, drafts, stale phrases
            for rx, what in PRIVATE:
                if re.search(rx, line):
                    bad(f'refers to {what}: {re.search(rx, line).group(0)!r}')
            for rx, what in DRAFT:
                if re.search(rx, line) and not in_code:
                    bad(f'{what}: {re.search(rx, line).group(0)!r}')
            if re.search(r'(?i)first draft', line) and not (rel == 'writeup/README.md' and line.startswith('|')):
                bad('"first draft" in the text')
            for rx, now in STALE:
                if re.search(rx, line):
                    bad(f'stale: {re.search(rx, line).group(0)!r} (now: {now})')
    for p in problems:
        print(p)
    print(f'{len(files)} files checked, {len(problems)} problem{"s" if len(problems) != 1 else ""}')
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
