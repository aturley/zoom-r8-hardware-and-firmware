#!/usr/bin/env python3
"""r8guard.py: a Claude Code PreToolUse hook (see .claude/settings.json) that enforces the R8 safety rules.

It reads the tool call as JSON on stdin and exits with 2 (blocked, the message goes back to the agent) when a
command would break one of the rules in writeup/02-safety-and-recovery.md:

  1. Never put R8BOOT.BIN, R8SUB.BIN or R8TEST.BIN on the card: the boot loader can flash them, and nothing we know
     recovers a bad boot loader or panel processor.
  2. No raw writes to a disk device (dd to /dev/disk*, diskutil erase/partition, newfs): one wrong number wipes a
     disk.
  3. PANEL-TX sends raw bytes to the panel processor, which is part of the recovery path. Only commands the stock
     firmware sends (appendix A). Allowed with R8_ALLOW_PANEL_TX=1 in the command, after the user agrees.
  4. SD-WRITE and SD-WRITES write any block, the filesystem included. Use SD-TEST or SD-WCMP (gap only), or
     r8card.py for files. Allowed with R8_ALLOW_RAW_SD=1 in the command, after the user agrees.
  5. No RELOAD, BYE or MSC-OFF while the card is mounted on this computer: eject it and confirm first.

Rules 3-5 apply only to commands that talk to the R8 (tools/fs.py or a serial terminal), so reading the
docs or grepping for these words is never blocked. Anything else passes (exit 0). Standard library only.
"""
import json, re, subprocess, sys


def block(msg):
    print('r8guard: ' + msg, file=sys.stderr)
    sys.exit(2)


def card_mounted():
    try:
        out = subprocess.run(['mount'], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    return [l.split(' on ')[1].split(' (')[0] for l in out.splitlines()
            if ' on /Volumes/' in l and ('msdos' in l or 'exfat' in l)]


def check_command(cmd):
    c = cmd
    if re.search(r'(?i)r8(boot|sub|test)\.bin', c) and re.search(r'/Volumes/|\bcp\b|\bmv\b|rsync|\bdd\b|>', c):
        block('R8BOOT.BIN, R8SUB.BIN and R8TEST.BIN must never go on the card: the R8 boot loader can flash '
              'them and nothing recovers a bad boot loader or panel processor (writeup section 2).')
    if re.search(r'\bdd\b[^|;&]*\bof=/dev/', c) or re.search(r'diskutil\s+(erase\w*|partition\w*|zero\w*|randomDisk)', c) \
            or re.search(r'\bnewfs\w*\b|\bmkfs\w*\b', c):
        block('raw writes to a disk device are not allowed here: one wrong device number destroys a disk. '
              'Copy files to the mounted card instead (tools/r8card.py deploy).')
    talks = re.search(r'fs\.py|/dev/(cu|tty)\.|/dev/ttyACM|\bscreen\b|\bminicom\b|\bpicocom\b|\bcu\s+-l', c)
    if not talks:                                    # only commands that send lines to the R8 (not grep, cat ...)
        return
    if re.search(r'\bPANEL-TX\b', c) and 'R8_ALLOW_PANEL_TX=1' not in c:
        block('PANEL-TX sends raw bytes to the panel processor, which is part of the recovery path. Send only '
              'commands the stock firmware sends (writeup appendix A), and only after the user agrees; then add '
              'R8_ALLOW_PANEL_TX=1 to the command.')
    if re.search(r'\bSD-WRITES?\b', c) and 'R8_ALLOW_RAW_SD=1' not in c:
        block('SD-WRITE and SD-WRITES write any block, including the filesystem. Use SD-TEST or SD-WCMP (they '
              'refuse anything outside the unpartitioned gap) or tools/r8card.py for files. If a raw write is '
              'really needed and the user agrees, add R8_ALLOW_RAW_SD=1 to the command.')
    if re.search(r'\b(RELOAD|BYE|MSC-OFF)\b', c) and 'r8card.py' not in c:
        vols = card_mounted()
        if vols:
            block(f'the card is still mounted on this computer ({", ".join(vols)}). Eject it and confirm it is gone '
                  '(diskutil eject, then check the volume has disappeared) before RELOAD, BYE or MSC-OFF '
                  '(writeup section 2, host computer lessons). tools/r8card.py deploy does all of this.')


def main():
    try:
        call = json.load(sys.stdin)
    except Exception:
        return
    tool, inp = call.get('tool_name', ''), call.get('tool_input', {}) or {}
    if tool == 'Bash':
        check_command(inp.get('command', ''))
    elif tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        p = inp.get('file_path', '') or ''
        if re.search(r'(?i)r8(boot|sub|test)\.bin$', p) and p.startswith('/Volumes/'):
            block('R8BOOT.BIN, R8SUB.BIN and R8TEST.BIN must never go on the card (writeup section 2).')


if __name__ == '__main__':
    main()
