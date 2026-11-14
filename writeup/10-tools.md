# 10. Tools

## Existing tools that helped

| Tool | Used for |
| --- | --- |
| TI C6000 Code Generation Tools 8.5.0 LTS (`cl6x`, `dis6x`, `ofd6x`) | Compiling C for the C674x; `dis6x` is the most reliable disassembler for this DSP. Free from TI. |
| Python 3 | Every analysis tool, image builder, simulator and test. No third-party packages were needed. |
| macOS `diskutil`, `fsck_msdos`, `hdiutil` | Checking the real card, and checking simulated card images attached as disk images |
| `screen` or any serial terminal | Talking to the Forth app over USB |
| A USB audio interface (Behringer UMC22) | Playing test signals into the R8 and recording its output |
| A phone camera (iPhone via macOS Continuity Camera) | Photographing the panel to map LEDs |

**What didn't help:** Ghidra 12.2 has no TI C6000 processor module (community ones exist; we did not try them). QEMU has no C6000 target. Neither would have replaced the board models in our own simulator, which is where most of the effort is.

## Tools we wrote

All are in `tools/` in this repository unless noted.

**Firmware files and static analysis**

| Tool | What it does |
| --- | --- |
| `r8fw.py` | Show the R8SYS.BIN header, checksum and AIS script; extract the sections; fix the checksum after edits |
| `xref.py`, `finfo.py`, `cluster.py`, `strref.py`, `gptr.py`, `dpref.py` | Cross-references, call trees, functions in a range, code that uses a string, peripheral pointer globals and the B14-relative globals |
| `dis.py` | Annotated listing of a stock function (our decoder, with `sim674`'s decoder as a fallback) |
| `mkelf6x.py`, `tidis.py` | Wrap a slice of the firmware in a minimal ELF so TI's `dis6x` can disassemble it; full listings saved in your own `fw/tidis_*.txt` (not included here) |
| `asm6x.py` | A small assembler for hand-written patches, emitting only encodings checked against real firmware words |

**Building firmware and apps**

| Tool | What it does |
| --- | --- |
| `cbuild/*/build.sh` | Compile and link with `cl6x` and our own minimal runtime |
| `standalone_c.py` | Wrap a C program in the stock header and boot commands as a standalone R8SYS.BIN |
| `appmenu.py` | Build the app-launcher firmware (stock plus a RUN APP menu item) |
| `mkapp.py` | Wrap an app as `APP.BIN` with the `R8AP` header (load address, length, entry, CRC-32) |
| `utilmenu.py` | Build the launcher firmware with RUN UTIL added to TOOL → SD CARD |
| `mkutil.py` | Wrap a utility (or `UTILMENU.SYS`) with the same header and magic `R8UT` |
| `mkfont.py` | Turn the font drawing `cbuild/forth/font.txt` (our own 5×7 design, one glyph per block of `#` and `.`) into `font.h`; it refuses two glyphs that look the same, since tests read the screen back by glyph. `--stock R8SYS.BIN` instead copies the stock font from your own 1.17 firmware into `font_stock.h`, which the app builds use when run with `FONT=stock` (`cbuild/font.sh`); keep such binaries to yourself |
| `celf.py` | Turn a linked ELF into a raw image |

**Simulation and tests**

| Tool | What it does |
| --- | --- |
| `sim674.py` | C674x simulator: execute packets, delay slots, interrupts, the compact instructions the compiler emits |
| `r8board.py` | Board models: PSC, GPIO, SPI1 with an ST7565 display, the panel link, McASP0 with its FIFOs and stall behaviour, timer 1, the interrupt controller, I2C0 and the clock chip, cache control |
| `r8sd.py`, `fat32img.py` | An SD card model (with the controller's 4 KB faults when DMATRIG is missing, and an error above 25 MHz) and a FAT32 image builder |
| `r8usb.py` | A USB host model that enumerates the app like a Mac does |
| `r8mock.py` | Stand-ins for the stock functions a utility calls (file API with the behaviour measured on the R8, including `*.*` matching and 8.3 aliases for long names; popups; UI events), so utilities run in the simulator without the stock firmware |
| `test_*.py` | One test per feature, each running the real firmware in the simulator; for example `test_looper.py`, `test_input.py`, `test_save.py`, `test_rate.py`, `test_sdread.py`; for utilities `test_utilmenu.py`, `test_utilboot.py`, `test_fileinfo.py`, `test_varispd.py`, `test_tonetest.py`, `test_reamp.py`; for apps `test_chooser.py`, `test_blippoo.py`, `test_padtest.py` |
| `run_tests.py` | Run every test (or those matching a word) and summarise: PASS, FAIL or SKIP, with a log per test |

**Running the tests.** Build first ([Getting started](getting-started.md), steps 3 and 4), plus the programs only the tests use, with `./cbuild/build_test_inputs.sh` (the standalone synth, the simulator's test programs, and a benchmark). `test_sim674.py` also compiles its test programs for the Mac with `clang` (Xcode's command line tools) to compare results. Then, from the top of the repository:

```sh
python3 tools/run_tests.py            # everything, one at a time
python3 tools/run_tests.py -j 4       # four at a time
python3 tools/run_tests.py sd msc     # only tests whose name contains "sd" or "msc"
python3 tools/test_sdwcmp.py          # one test directly; it prints PASS or FAIL per check
```

- Each test prints one line per check and ends with `FAILURES: none` or the list of failed checks. The runner counts a test as passed when it exits with 0 and prints no `FAIL` line, and keeps each test's full output in `tools/out/test-logs/`.
- **Slow.** The simulator runs a few hundred thousand DSP cycles a second, so a test that boots an app and talks to it over USB takes minutes. From a clean copy on our Mac (14 November), the 42 tests took 1 h 28 min four at a time; `test_battery.py` alone took 43 minutes.
- **Some tests need files you supply.** Four read captures from our unit in `data/`, which aren't published because they are dumps of the stock firmware's memory or recordings: `test_appmenu.py`, `test_revmenu.py` and `test_utilmenu.py` (`MENUDUMP.BIN`, a snapshot of the stock menus) and `test_cfilerev.py` (`REVIN_R8.WAV`). Without them the runner reports SKIP rather than FAIL. Everything else needs only `fw/R8SYS.BIN` or nothing.
- **Run them from the runner, or from `tools/`.** A few older tests import their neighbours from the current directory, so `python3 -I`, or running them from elsewhere, fails with `ModuleNotFoundError`. The runner always runs tests from `tools/`.
- A failure on the R8 that the simulator didn't catch usually means a model is wrong. Fix the model to match the hardware (as `r8sd.py` was for DMATRIG), then the firmware.

A trap in the tests: `sim.mocks` replaces a function by its **address**. When a test chains apps (the chooser loading the Forth app over the same L2), a mock left on the first app's address fires inside whatever code the second app put there. In `test_chooser.py` the chooser's `delay_us` mock landed in the middle of the Forth app's `status()`, acted as an early return, and the app looped forever before starting USB; it only showed up when a rebuild moved the code. Drop the first app's mocks when the trampoline runs, and mock the second app's functions by its own symbol table.

**Bench tools**

| Tool | What it does |
| --- | --- |
| `ears.swift`, `hear.py` | Play a WAV and record at the same time on a named audio device; measure tones and levels |
| Camera scripts (not included) | We watched the panel through an iPhone used as a Mac webcam (Continuity Camera), taking a still on demand and diffing two stills to find which LED had just lit. They were small one-off scripts tied to our setup, so they are not in this repository; any webcam and an image diff do the same job. They saved asking someone to look at the panel for every LED test |
| `fs.py` | Send lines to the Forth app over USB serial and print the replies (`python3 tools/fs.py '1 2 + .'`); `--ready S` waits for the app after `RELOAD`, `-w` waits longer for slow words, and a disconnect after `MSC-RW`, `MSC-OFF`, `RELOAD` or `BYE` is reported as expected. Standard library only |
| `r8card.py` | `status`: what is on the card against your builds (checksums), and the backups. `deploy`: build, copy, check, eject, confirm the eject, `RELOAD`, in one command that stops at the first problem (getting started, step 8). Through a running Forth-core app; macOS |
| `doccheck.py` | Check the writeup: broken links and anchors, paths that don't exist, references to unpublished material, placeholders, and phrases known to be out of date (a list in the script: add the old wording whenever a fact changes) |
| PADTEST (an app, `cbuild/padtest`) | Show panel events on the LCD and log every panel byte to the card |

**On the R8: the Forth app.** Section 8 describes it. For exploration, the most-used words were `@` and `!` for registers, `DUMP`, `SD-READ`, `PANEL-MON` (decode panel traffic live) and `PANEL-TX` (send panel commands).

**Working with Claude Code.** The repository's `.claude/settings.json` runs `tools/r8guard.py` before every shell command and file write an agent makes, and blocks the ones that break the safety rules of section 2:
- putting `R8BOOT.BIN`, `R8SUB.BIN` or `R8TEST.BIN` on the card;
- raw writes to a disk device;
- `PANEL-TX` (unless the command carries `R8_ALLOW_PANEL_TX=1`, after the user agrees);
- `SD-WRITE`/`SD-WRITES` (unless it carries `R8_ALLOW_RAW_SD=1`);
- `RELOAD`, `BYE` or `MSC-OFF` while the card is still mounted on the computer.

The agent gets the reason back, and nothing runs. The `r8-firmware` skill (`.claude/skills/r8-firmware/SKILL.md`) tells an agent the rest: the person does the physical steps, look things up before experimenting, the build-test-deploy loop with these tools, how to label claims, and what must never enter the repository. The rules about the R8 apply only to commands that talk to it, so reading or searching the docs is never blocked. Hooks enforce; written instructions only ask.
