#!/usr/bin/env python3
"""fs.py: send lines to the R8 Forth app over its USB serial port and print the replies.

    python3 tools/fs.py '1 2 + .' 'WORDS'          send two lines, print each reply
    python3 tools/fs.py -w 10 '1000 128 7 SD-TEST .'  wait up to 10 s for a slow word
    python3 tools/fs.py --ready 30                 wait (up to 30 s) until the app answers, e.g. after RELOAD
    python3 tools/fs.py -p /dev/ttyACM0 '.RATE'     name the port yourself

The app is a USB CDC serial device; the line speed setting does not matter. On macOS it appears as
/dev/cu.usbmodemR8F11, on Linux usually as /dev/ttyACM0 (Linux untested). Without -p the first match of those is used.

MSC-RW, MSC-RO, MSC-OFF, RELOAD and BYE make the app reconnect, so the port disappears in the middle of the reply.
That is reported and is not an error. For an interactive session use a terminal program instead, for example
`screen /dev/cu.usbmodemR8F11` (Ctrl-A K to quit). Standard library only.
"""
import argparse, glob, os, select, sys, termios, time

RECONNECTS = ('MSC-RW', 'MSC-RO', 'MSC-OFF', 'RELOAD', 'BYE')


def find_port():
    for pat in ('/dev/cu.usbmodemR8*', '/dev/ttyACM*'):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    a = termios.tcgetattr(fd)
    a[0] = 0; a[1] = 0; a[3] = 0                     # raw: no input/output processing, no echo
    a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def read_reply(fd, wait, quiet=0.3):
    """Read until `quiet` seconds pass with no data (after at least one byte), or `wait` seconds in all."""
    out = b''; end = time.time() + wait; last = None
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            b = os.read(fd, 4096)
            if b:
                out += b; last = time.time()
        if last and time.time() - last > quiet and (out.endswith(b'ok\r\n') or out.endswith(b'ok\n')):
            break
    return out.decode('latin1')


def ready(path, seconds):
    end = time.time() + seconds
    while time.time() < end:
        p = path or find_port()
        if p and os.path.exists(p):
            try:
                fd = open_port(p); read_reply(fd, 0.3)
                os.write(fd, b'1 .\r')
                ok = ' 1  ok' in read_reply(fd, 1.0)
                os.close(fd)
                if ok:
                    return p
            except OSError:
                pass
        time.sleep(0.5)
    return None


def main():
    ap = argparse.ArgumentParser(description='Send lines to the R8 Forth app and print the replies.')
    ap.add_argument('lines', nargs='*', help='Forth lines to send, one per argument')
    ap.add_argument('-p', '--port', help='serial device (default: first /dev/cu.usbmodemR8* or /dev/ttyACM*)')
    ap.add_argument('-w', '--wait', type=float, default=2.0, help='seconds to wait for each reply (default 2)')
    ap.add_argument('--ready', type=float, metavar='S', help='first wait up to S seconds for the app to answer')
    args = ap.parse_args()

    port = args.port
    if args.ready is not None:
        port = ready(port, args.ready)
        if not port:
            sys.exit(f'no answer from the Forth app within {args.ready:g} s')
        print(f'ready on {port}')
    port = port or find_port()
    if not port:
        sys.exit('no R8 serial port found: is the Forth app running and the USB cable connected? (or use -p)')

    fd = open_port(port)
    read_reply(fd, 0.3)                                  # drop anything left over
    line = ''
    try:
        for line in args.lines:
            os.write(fd, (line + '\r').encode('latin1'))
            print('>', line)
            print(read_reply(fd, args.wait).strip())
    except OSError:
        if any(w.upper() in RECONNECTS for w in line.split()):
            print('(the app reconnected, as expected after ' + ' / '.join(RECONNECTS) + ')')
        else:
            sys.exit('the serial port went away')
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == '__main__':
    main()
