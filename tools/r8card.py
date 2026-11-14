#!/usr/bin/env python3
"""r8card.py: what is on the R8's card, and putting new builds there, through a running Forth-core app.

    python3 tools/r8card.py status                 card files vs local builds (mounts read-only if needed)
    python3 tools/r8card.py deploy forth sampler   copy builds to the card, check, eject, RELOAD
    python3 tools/r8card.py deploy --build forth   build first
    python3 tools/r8card.py deploy --no-reload utils

Names: chooser, forth, sampler, blippoo, padtest, powerlog, utils (UTILMENU.SYS and every utility), launcher
(out/R8SYS.BIN to the card root; flashing it is still the R8's own update, by hand), or "apps" (the six apps).

The R8 must be running the Forth app or the sampler (they provide USB storage and RELOAD), connected by USB.
deploy does, in order, and stops at the first problem: build (--build) -> MSC-RW -> wait for the card to mount ->
back up every file it will replace to data/card-backup/<time>/ -> copy -> compare SHA-256 -> eject -> confirm the
volume is gone -> MSC-OFF -> RELOAD -> wait for the app. It never RELOADs unless the eject is confirmed, and refuses
to write R8BOOT.BIN, R8SUB.BIN or R8TEST.BIN. macOS only (diskutil). Standard library only.
"""
import argparse, glob, hashlib, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fs  # noqa: E402  (tools/fs.py: port discovery and line I/O)

APPS = {   # name: [(local build output, path on the card)]
    'chooser':  [('cbuild/chooser/out/APP.BIN', 'APP.BIN')],
    'forth':    [('cbuild/forth/out/APP.BIN', 'R8APPS/FORTH.BIN')],
    'sampler':  [('cbuild/sampler/out/SAMPLER.BIN', 'R8APPS/SAMPLER.BIN')],
    'blippoo':  [('cbuild/blippoo/out/BLIPPOO.BIN', 'R8APPS/BLIPPOO.BIN')],
    'padtest':  [('cbuild/padtest/out/PADTEST.BIN', 'R8APPS/PADTEST.BIN')],
    'powerlog': [('cbuild/powerlog/out/POWERLOG.BIN', 'R8APPS/POWERLOG.BIN')],
    'launcher': [('out/R8SYS.BIN', 'R8SYS.BIN')],
}
BUILD = {'chooser': 'cbuild/chooser/build.sh', 'forth': 'cbuild/forth/build.sh', 'sampler': 'cbuild/sampler/build.sh',
         'blippoo': 'cbuild/blippoo/build.sh', 'padtest': 'cbuild/padtest/build.sh', 'powerlog': 'cbuild/powerlog/build.sh',
         'utils': 'cbuild/utils/build.sh'}
FORBIDDEN = ('R8BOOT.BIN', 'R8SUB.BIN', 'R8TEST.BIN')   # boot loader, panel processor, test image: never ours to write


def utils_files():
    out = [('cbuild/utils/out/UTILMENU.SYS', 'R8UTILS/UTILMENU.SYS')]
    for p in sorted(glob.glob(os.path.join(ROOT, 'cbuild/utils/out/*.BIN'))):
        out.append((os.path.relpath(p, ROOT), 'R8UTILS/' + os.path.basename(p)))
    return out


def files_for(names):
    out = []
    for n in names:
        if n == 'apps':
            out += files_for(['chooser', 'forth', 'sampler', 'blippoo', 'padtest', 'powerlog'])
        elif n == 'utils':
            out += utils_files()
        elif n in APPS:
            out += APPS[n]
        else:
            sys.exit(f'unknown name {n!r}; use: ' + ', '.join(list(APPS) + ['utils', 'apps']))
    return out


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:8]


def say(msg):
    print(msg, flush=True)


# ---- talking to the app
def forth(line, wait=3.0):
    """Send one line; returns the reply, or None if the app reconnected while answering (MSC-*, RELOAD)."""
    port = fs.find_port()
    if not port:
        return None
    try:
        fd = fs.open_port(port)
    except OSError:
        return None
    try:
        fs.read_reply(fd, 0.2)
        os.write(fd, (line + '\r').encode('latin1'))
        return fs.read_reply(fd, wait)
    except OSError:
        return None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def need_app(seconds=15):
    if not fs.ready(None, seconds):
        sys.exit('no answer from the Forth app over USB: start FORTH or SAMPLER from the chooser and connect USB')


# ---- the card on the Mac
def mounted(vol):
    return os.path.isdir(vol) and any(f' on {vol} ' in l for l in subprocess.run(['mount'], capture_output=True,
                                                                                  text=True).stdout.splitlines())


def read_only(vol):
    return any(f' on {vol} ' in l and 'read-only' in l for l in subprocess.run(['mount'], capture_output=True,
                                                                               text=True).stdout.splitlines())


def disk_listed(name):
    return name in subprocess.run(['diskutil', 'list', 'external'], capture_output=True, text=True).stdout


def wait_mount(vol, name, seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        if mounted(vol):
            return True
        out = subprocess.run(['diskutil', 'list', 'external'], capture_output=True, text=True).stdout
        for l in out.splitlines():
            if name in l:
                subprocess.run(['diskutil', 'mount', l.split()[-1]], capture_output=True)
        time.sleep(1)
    return mounted(vol)


def eject(vol, name):
    for _ in range(5):
        subprocess.run(['diskutil', 'eject', vol], capture_output=True)
        time.sleep(1)
        if not os.path.isdir(vol) and not disk_listed(name):
            return True
        time.sleep(2)
    return False


def storage_off():
    for _ in range(3):
        need_app(30)
        r = forth('MSC-OFF')
        if r and 'storage off' in r:
            return True
        time.sleep(2)
    return False


# ---- commands
def status(args):
    vol = args.volume
    we_mounted = False
    if not mounted(vol):
        need_app()
        say('card not mounted: turning on read-only USB storage (MSC-RO)')
        forth('MSC-RO')
        if not wait_mount(vol, os.path.basename(vol)):
            sys.exit(f'{vol} did not mount')
        we_mounted = True
    rows = []
    for local, card in files_for(['apps', 'utils', 'launcher']):
        lp, cp = os.path.join(ROOT, local), os.path.join(vol, card)
        lh = sha(lp) if os.path.exists(lp) else None
        ch = sha(cp) if os.path.exists(cp) else None
        state = ('same' if lh == ch else 'DIFFERS') if lh and ch else ('not on card' if lh else
                                                                       ('not built' if ch else 'neither'))
        rows.append((card, ch or '-', lh or '-', state))
    extra = [p for p in glob.glob(os.path.join(vol, 'R8APPS', '*.BIN')) + glob.glob(os.path.join(vol, 'R8UTILS', '*'))
             if os.path.relpath(p, vol) not in {r[0] for r in rows} and not os.path.basename(p).startswith('._')]
    w = max(len(r[0]) for r in rows)
    say(f'{"on the card":<{w}}  card      local     state')
    for r in rows:
        say(f'{r[0]:<{w}}  {r[1]:<8}  {r[2]:<8}  {r[3]}')
    for p in sorted(extra):
        say(f'{os.path.relpath(p, vol):<{w}}  {sha(p):<8}  -         only on the card')
    bad = [n for n in FORBIDDEN if os.path.exists(os.path.join(vol, n))]
    if bad:
        say('WARNING: on the card: ' + ', '.join(bad) + ' -- the R8 boot loader can flash these; remove them')
    backups = sorted((p for p in glob.glob(os.path.join(ROOT, 'data', 'card-backup', '*')) if os.path.isdir(p)),
                     key=os.path.getmtime)
    if backups:
        say(f'backups: {len(backups)} folders in data/card-backup, newest {os.path.basename(backups[-1])}')
    if we_mounted:
        if not eject(vol, os.path.basename(vol)):
            sys.exit(f'could not eject {vol}: eject it on the Mac, then run: python3 tools/fs.py MSC-OFF')
        storage_off()
        say('ejected and storage off again')


def deploy(args):
    pairs = files_for(args.names)
    if args.build:
        names = {n for n in args.names if n in BUILD}
        if 'apps' in args.names:
            names |= {'chooser', 'forth', 'sampler', 'blippoo', 'padtest', 'powerlog'}
        for n in sorted(names):
            say(f'build {n}')
            r = subprocess.run([os.path.join(ROOT, BUILD[n])], capture_output=True, text=True)
            if r.returncode:
                sys.exit(f'build of {n} failed:\n' + (r.stdout + r.stderr)[-2000:])
        pairs = files_for(args.names)
    for local, card in pairs:
        if os.path.basename(card).upper() in FORBIDDEN:
            sys.exit(f'refusing to write {card}')
        if not os.path.exists(os.path.join(ROOT, local)):
            sys.exit(f'{local} is not built (use --build, or run its build script)')
    vol, name = args.volume, os.path.basename(args.volume)
    if not mounted(vol):
        need_app()
        say('MSC-RW: card to the Mac, writable')
        forth('MSC-RW')
        if not wait_mount(vol, name):
            sys.exit(f'{vol} did not mount (check the USB cable; `diskutil list external`)')
    if read_only(vol):
        sys.exit(f'{vol} is mounted read-only: eject it, then run MSC-OFF and try again')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    bdir = os.path.join(ROOT, 'data', 'card-backup', stamp)
    for local, card in pairs:
        cp = os.path.join(vol, card)
        if os.path.exists(cp):
            os.makedirs(os.path.dirname(os.path.join(bdir, card)), exist_ok=True)
            shutil.copy2(cp, os.path.join(bdir, card))
    if os.path.isdir(bdir):
        say(f'backup: data/card-backup/{stamp}')
    for local, card in pairs:
        cp = os.path.join(vol, card)
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, local), cp)
    os.sync()
    bad = []
    for local, card in pairs:
        a, b = sha(os.path.join(ROOT, local)), sha(os.path.join(vol, card))
        say(f'  {card:<24} {a}  {"ok" if a == b else "MISMATCH " + b}')
        if a != b:
            bad.append(card)
    if bad:
        sys.exit('checksum mismatch: the card is still mounted; nothing reloaded. Copy again or restore the backup.')
    if not eject(vol, name):
        sys.exit(f'eject did not complete ({vol} still present): not reloading. Close anything using the card '
                 '(Spotlight, Finder windows), eject it on the Mac, then: python3 tools/fs.py MSC-OFF RELOAD')
    say('ejected (confirmed)')
    if not storage_off():
        sys.exit('MSC-OFF did not answer "storage off": not reloading')
    if any(card == 'R8SYS.BIN' for _, card in pairs):
        say('launcher copied: flash it with the R8 update (AC adapter, hold PLAY at power-on, LEFT then ENTER)')
    if args.no_reload:
        say('done (no RELOAD)')
        return
    forth('RELOAD', wait=5)
    p = fs.ready(None, 40)
    if not p:
        sys.exit('RELOAD sent, but the app did not answer within 40 s: look at the R8 screen')
    say('reloaded; the app answers on ' + p)


def main():
    ap = argparse.ArgumentParser(description='Card status and deployment for the R8 (via a running Forth-core app).')
    ap.add_argument('--volume', default='/Volumes/ZOOM_R8', help='the card as mounted on the Mac (default /Volumes/ZOOM_R8)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status', help='card files against local builds')
    d = sub.add_parser('deploy', help='copy builds to the card, check, eject, RELOAD')
    d.add_argument('names', nargs='+')
    d.add_argument('--build', action='store_true', help='run the build scripts first')
    d.add_argument('--no-reload', action='store_true', help="stop after MSC-OFF (for example when deploying an app that isn't running)")
    args = ap.parse_args()
    (status if args.cmd == 'status' else deploy)(args)


if __name__ == '__main__':
    main()
