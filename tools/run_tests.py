#!/usr/bin/env python3
"""run_tests.py: run the simulator tests (tools/test_*.py) and summarise them.

    python3 tools/run_tests.py                 every test, one at a time
    python3 tools/run_tests.py -j 4            four at a time (the full suite takes about an hour on one core)
    python3 tools/run_tests.py sd msc forth    only tests whose name contains one of these words

Build first: most tests load the firmware from cbuild/*/out (see writeup/getting-started.md). Some read your stock
firmware from fw/ or captures from a real unit in data/; without them those tests are reported as SKIP.

Each test runs from the tools/ directory (some older tests import their neighbours from the current directory).
A test passes when it exits with 0 and prints no "FAIL" line. Its full output goes to tools/out/test-logs/<name>.log.
Standard library only.
"""
import argparse, concurrent.futures, glob, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, 'out', 'test-logs')


def run_one(path):
    name = os.path.basename(path)[:-3]
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.basename(path)], cwd=HERE, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = p.stdout.decode('utf-8', 'replace')
    with open(os.path.join(LOGS, name + '.log'), 'w') as f:
        f.write(out)
    fails = [l for l in out.splitlines() if l.startswith('FAIL ')]
    missing = 'No such file or directory' in out and any(d in out for d in ('/fw/', '/data/', 'fw/', 'data/'))
    if p.returncode == 0 and not fails and 'Traceback' not in out:
        status = 'PASS'
    elif missing and not fails:
        status = 'SKIP'
    else:
        status = 'FAIL'
    detail = fails[:3] if fails else ([l for l in out.splitlines() if l.strip()][-1:] if status != 'PASS' else [])
    if status == 'FAIL' and 'No such file or directory' in out and '/cbuild/' in out:
        detail.append('(not built: run the app\'s cbuild/<app>/build.sh, or cbuild/build_test_inputs.sh)')
    return name, status, time.time() - t0, detail


def main():
    ap = argparse.ArgumentParser(description='Run the R8 simulator tests.')
    ap.add_argument('words', nargs='*', help='only tests whose name contains one of these')
    ap.add_argument('-j', '--jobs', type=int, default=1, help='tests to run at once (default 1)')
    args = ap.parse_args()
    tests = sorted(glob.glob(os.path.join(HERE, 'test_*.py')))
    if args.words:
        tests = [t for t in tests if any(w in os.path.basename(t) for w in args.words)]
    if not tests:
        sys.exit('no tests match')
    os.makedirs(LOGS, exist_ok=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for name, status, secs, detail in ex.map(run_one, tests):
            print(f'{status}  {name}  ({secs:.0f} s)', flush=True)
            for d in detail:
                print(f'      {d}')
            results.append(status)
    n = {s: results.count(s) for s in ('PASS', 'FAIL', 'SKIP')}
    print(f"\n{n['PASS']} passed, {n['FAIL']} failed, {n['SKIP']} skipped; logs in {os.path.relpath(LOGS)}")
    sys.exit(1 if n['FAIL'] else 0)


if __name__ == '__main__':
    main()
