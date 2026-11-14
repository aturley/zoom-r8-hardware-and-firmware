---
name: r8-firmware
description: Use when working on Zoom R8 custom firmware in this repository - building, testing or deploying apps, utilities or the launcher, running code on the R8 over USB, investigating the stock firmware or hardware, or editing the writeup.
---

# Working on Zoom R8 firmware

This repository runs custom code on a Zoom R8 (stock firmware 1.17, a TI C6748 DSP). The writeup in `writeup/` is the reference; this skill is how to work with it safely and cheaply. Read `writeup/02-safety-and-recovery.md` once before anything touches the R8.

## The person has the R8, you don't

The person you are working with does every physical step: flashing (hold PLAY at power-on), pressing keys, starting apps from the chooser, watching the LCD and LEDs, listening. Before each hardware step say exactly what to do and what you expect to see, then wait for their report. Never claim something worked on hardware until they have told you so.

## Safety rules

The hook in `.claude/settings.json` (`tools/r8guard.py`) blocks the first four. If it blocks you, don't work around it: explain to the person and let them decide.

1. Never put `R8BOOT.BIN`, `R8SUB.BIN` or `R8TEST.BIN` on the card. A bad boot loader or panel processor can't be recovered.
2. No raw writes to disk devices (`dd of=/dev/...`, `diskutil erase...`).
3. `PANEL-TX` only with commands the stock firmware sends (`writeup/appendix-a-panel-protocol.md`), only after the person agrees, then with `R8_ALLOW_PANEL_TX=1` in the command.
4. Raw block writes (`SD-WRITE`, `SD-WRITES`) only after the person agrees, then with `R8_ALLOW_RAW_SD=1`. Prefer `SD-WCMP`, which reads the partition table and refuses anything outside the unpartitioned gap, or `SD-TEST`, which only allows blocks 1–8191 (outside the filesystem only if the first partition starts at 8192 or later; check with `0 4 5 SD-WCMP .` in the Forth app, which prints the gap and writes nothing).
5. Never `RELOAD`, `BYE` or `MSC-OFF` with the card still mounted on the computer. `tools/r8card.py deploy` confirms the eject for you.

Not enforced, still mandatory:
- Never write the flash's BOOT region from code; never call stock flash routines on regions other than SYSTEM.
- A new firmware image (`R8SYS.BIN`) is only flashed by the person, with the AC adapter, through the R8's own update. The way back is Zoom's `R8SYS.BIN` and the same procedure.
- Ask before writing anything to the card other than through `r8card.py deploy`.

## Before an experiment, look it up

Most questions are already answered. Search before building a test:
- `writeup/06-quirks.md` before touching the SD card, McASP audio, I2C, USB or the panel link.
- `writeup/appendix-b-addresses.md` for stock addresses and registers. They are for 1.17 only. Grep it; it is long.
- `writeup/appendix-a-panel-protocol.md` for keys, faders, pads and LEDs.
- `writeup/08-forth-app.md` for the Forth words that already exist.
- `writeup/12-open-questions.md` for what is known to be unknown.
- Zoom's operation manual for what a control is supposed to do: https://zoomcorp.com/documents/148/E_R8.pdf

If you run an experiment anyway, write down beforehand what result you expect.

## Choosing how to run code

`writeup/07-building-custom-firmware.md` describes four ways. In short:
- **App** (`cbuild/<app>`, loaded from `A:\R8APPS` by the chooser): owns the whole machine. Start from `cbuild/forth` (the core plus ad hoc words in `main.c`) or `cbuild/sampler` (an app built on the core through its hooks).
- **Utility** (`cbuild/utils`, run from RUN UTIL): runs inside the stock firmware and returns to its menu; uses stock files, menus and audio.
- **Patch** to the stock firmware, or a **standalone image**: needs a reflash for every change, so only when the other two can't do it.

## The loop

```sh
./cbuild/forth/build.sh                          # each app has its own build.sh; utilities: cbuild/utils/build.sh
python3 tools/run_tests.py forth sd              # tests whose names match; the full suite takes over an hour
python3 tools/r8card.py deploy --build forth     # copy, check, eject, confirm, RELOAD (the R8 must be running FORTH or SAMPLER)
python3 tools/fs.py '1 2 + .' 'WORDS'            # talk to the running app; -w N for slow words, --ready N after a reload
python3 tools/r8card.py status                   # what is on the card against your builds
```

- Run the matching simulator tests before every hardware step. They use the real binaries against board models (`writeup/10-tools.md`).
- When the R8 disagrees with the simulator, fix the model in `tools/` until the old build fails the same way, then fix the code.
- Apps must fit L2: check the link map's end against 0x1182C000 (the sampler has little room left).
- Mocks in the simulator are keyed by address. If a test loads one app from another, drop the first app's mocks when the second takes over.
- To time something on the R8, wrap it in `TICKS` (360 MHz) in Forth; `writeup/08-forth-app.md` has an example.

## Writing it down

- Label every hardware claim: **confirmed on hardware**, **from the firmware**, or **inferred**. Never upgrade a label without new evidence.
- After changing a fact, update the writeup where it is stated (grep for it). Add the old wording to the `STALE` list in `tools/doccheck.py`, then run `python3 tools/doccheck.py`.
- The writeup is public: no references to private notes or local paths, and a plain tone.

## What must not enter the repository

- Zoom's files or data: `fw/` and `data/` are git-ignored for this reason, as are build outputs. The launcher image is built from the person's own `R8SYS.BIN`.
- Large excerpts of stock disassembly.
- Builds made with `FONT=stock` contain Zoom's font: for the person's own use, never shared.

Commit or push only when asked.
