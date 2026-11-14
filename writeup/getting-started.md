# Getting started: from a fresh clone to Forth on your R8

This walks through building everything in this repository and running the Forth app on your own R8. It takes you from nothing to a prompt over USB, then shows the loop we used to try new code without taking the card out.

Read [Safety and recovery](02-safety-and-recovery.md) first. Nothing here writes the boot loader or the panel processor's firmware, and the R8's own update procedure brings back Zoom's firmware at any time.

**Status (14 November 2026).** We ran every build step below from a clean copy of the repository on a Mac, and the apps it produced were byte-identical to the ones on our R8. Later the same day the apps moved to a font of our own, which changes their bytes; those builds pass every test and were checked on the R8. The launcher firmware from a clean build is byte-identical to the one on our unit; we flashed it again from this build and checked RUN APP, RUN UTIL (HELLO), REVERSE and the chooser's EXIT. So everything this walkthrough builds has run on the R8.

## What you end up with

- **The launcher firmware:** Zoom's firmware 1.17 with three menu items added. TOOL → SD CARD gains RUN APP and RUN UTIL, and TRACK EDIT gains REVERSE. Everything else behaves as stock. You build it from your own copy of Zoom's firmware, because this repository can't include Zoom's code (see [README](README.md#publishing)).
- **The app chooser** as `A:\APP.BIN`. RUN APP starts it, and it lists the apps in `A:\R8APPS`.
- **Apps** in `A:\R8APPS`: FORTH (an interactive Forth over USB), SAMPLER (a four-loop sampler on the Forth core), BLIPPOO (a Blippoo Box emulation), PADTEST (shows and logs panel events) and POWERLOG (logs the battery and power readings).
- **Utilities** in `A:\R8UTILS`. RUN UTIL lists them, and they run inside the stock firmware and return to its menu.

## 1. What you need

| Item | Notes |
| --- | --- |
| A Zoom R8 running firmware **1.17** | Check with TOOL → SYSTEM → VERSION (manual p. 114). The patch tools refuse any other version. |
| Zoom's **R8SYS.BIN for 1.17** | From Zoom's R8 firmware download. Its SHA-256 starts with `6e4bf64e`. Keep a copy: it is also how you go back. |
| The R8's **AC adapter** | The manual asks for it during any firmware update. |
| An **SD card** | Use one you can afford to reformat, or back it up first. |
| A **computer** | We used macOS. The tools are plain Python and shell; Linux should work for building, but we haven't tried it. |
| **TI C6000 Code Generation Tools 8.5.0 LTS** | Free from TI. The build scripts expect them in `/Applications/ti/ti-cgt-c6000_8.5.0.LTS`; anywhere else, set `CGT` to the install directory. |
| **Python 3** | Standard library only (we used 3.9). |
| A **USB cable** for the R8's mini-B port | Used for the Forth console and to copy files. |

## 2. Put your stock firmware in `fw/`

At the top of the repository:

```sh
mkdir fw
cp /path/to/R8SYS.BIN fw/R8SYS.BIN
shasum -a 256 fw/R8SYS.BIN        # must start with 6e4bf64e
```

Git ignores `fw/`. The launcher is a patch of this file, and a few tests read it. The apps and utilities don't need it.

## 3. Build the launcher firmware

```sh
./cbuild/launcher/build.sh
python3 -I tools/utilmenu.py fw/R8SYS.BIN cbuild/launcher/out/launch.out out/R8SYS.BIN
```

The first command compiles the launcher with `cl6x`. The first build also compiles the few runtime helpers we take from TI's sources (`cbuild/out/r8rts.lib`). The second patches your firmware. It checks that the input is stock 1.17 and that only the expected bytes changed, then writes `out/R8SYS.BIN` and prints its SHA-256.

## 4. Build the apps and utilities

```sh
for a in chooser forth sampler blippoo padtest powerlog utils; do ./cbuild/$a/build.sh || break; done
```

| Output | Goes on the card as |
| --- | --- |
| `cbuild/chooser/out/APP.BIN` | `APP.BIN` (card root) |
| `cbuild/forth/out/APP.BIN` | `R8APPS/FORTH.BIN` |
| `cbuild/sampler/out/SAMPLER.BIN` | `R8APPS/SAMPLER.BIN` |
| `cbuild/blippoo/out/BLIPPOO.BIN` | `R8APPS/BLIPPOO.BIN` |
| `cbuild/padtest/out/PADTEST.BIN` | `R8APPS/PADTEST.BIN` |
| `cbuild/powerlog/out/POWERLOG.BIN` | `R8APPS/POWERLOG.BIN` |
| `cbuild/utils/out/UTILMENU.SYS` | `R8UTILS/UTILMENU.SYS` (required for RUN UTIL) |
| `cbuild/utils/out/*.BIN` | `R8UTILS/` (any you want: FILEINFO, VARISPD, TONETEST, REAMP, BATTINFO, KEYLOG, HELLO, LISTDIAG) |

Each script fails loudly if the compiler emits instructions our loader and simulator don't handle (compact instructions or SPLOOP).

**Optional: the stock font.** The apps use a 5×7 font of our own. If you prefer the R8's own font, build with `FONT=stock` (for example `FONT=stock ./cbuild/forth/build.sh`): it copies the font out of your `fw/R8SYS.BIN`. Those binaries then contain Zoom's font data, so keep them for your own use and don't share them. The simulator tests read the screen with our font, so run them on default builds.

## 5. Prepare the card

Copy the files onto the card, either with the R8 in its own card-reader mode (USB, manual p. 103) or in a card reader on your computer:

```
R8SYS.BIN          the launcher firmware from step 3 (out/R8SYS.BIN)
APP.BIN            the chooser
R8APPS/            FORTH.BIN, SAMPLER.BIN, BLIPPOO.BIN, PADTEST.BIN, POWERLOG.BIN
R8UTILS/           UTILMENU.SYS and the utilities
```

Create `R8APPS` and `R8UTILS` yourself. Leave the `ZOOM_R8` folder alone: it holds your projects. Eject the card properly before taking it out or switching the R8 off.

## 6. Install the launcher firmware

This is the R8's normal firmware update (manual p. 114):

1. With the card in the R8 and the AC adapter connected, hold **PLAY** and switch the power on.
2. The updater asks for confirmation with cancel selected: press LEFT to select OK, then ENTER. The update starts. Don't switch off until it says it has finished.
3. Switch the R8 off, then on again normally.

The R8 now starts as usual. TOOL → SD CARD shows RUN APP and RUN UTIL after the stock items. You can leave `R8SYS.BIN` on the card; the update only runs when PLAY is held at power-on.

## 7. Start the Forth app

1. TOOL → SD CARD → RUN APP. The chooser lists the apps in `R8APPS`.
2. Select FORTH.BIN with UP/DOWN or the wheel, then press ENTER. The screen shows the app's status and console.
3. Connect the R8 to your computer by USB. The app appears as a serial port: `/dev/cu.usbmodemR8F11` on a Mac.
4. Talk to it:

```sh
python3 -I tools/fs.py --ready 10 '1 2 + .' 'WORDS'
screen /dev/cu.usbmodemR8F11          # interactive; Ctrl-A K quits
```

`fs.py` sends each argument as a line and prints the reply. [Section 8](08-forth-app.md) describes the app and its words. Ctrl-C stops a running word.

To leave, type `BYE`, or press EXIT in the chooser. Both restart the R8 into its normal firmware.

## 8. The development loop: new code without taking the card out

Once the Forth app (or the sampler) is running, a new build reaches the card over USB with one command:

```sh
python3 tools/r8card.py deploy --build forth       # build, copy, check, eject, RELOAD
python3 tools/r8card.py deploy blippoo utils       # several at once; names: apps, utils, launcher, or one app
python3 tools/r8card.py status                     # what is on the card against your builds
```

`deploy` turns on writable USB storage (`MSC-RW`), waits for the card to mount, backs up every file it replaces to `data/card-backup/`, copies, compares checksums, ejects, **confirms the volume is gone**, turns storage off and sends `RELOAD`. It stops at the first problem, and never reloads unless the eject is confirmed. By hand, the same steps are:

```sh
python3 -I tools/fs.py MSC-RW                     # the card appears on the computer, writable
cp cbuild/forth/out/APP.BIN /Volumes/ZOOM_R8/R8APPS/FORTH.BIN
shasum -a 256 cbuild/forth/out/APP.BIN /Volumes/ZOOM_R8/R8APPS/FORTH.BIN    # must match
diskutil eject /Volumes/ZOOM_R8
ls /Volumes/ZOOM_R8                                # must fail: confirm the eject really happened
python3 -I tools/fs.py --ready 20 MSC-OFF
python3 -I tools/fs.py RELOAD                      # loads the new file and runs it
```

- The volume name is your card's label (`ZOOM_R8` on ours; `r8card.py --volume` for another). If the card doesn't mount by itself after `MSC-RW`, `r8card.py` mounts it; by hand, `diskutil list external` shows it and `diskutil mount` mounts it.
- **Confirm the eject before `RELOAD`.** Once, Spotlight held the volume and the eject silently failed ([section 2](02-safety-and-recovery.md#host-computer-lessons)).
- `RELOAD` reloads the file the chooser started, so the same steps work for any app built on the Forth core (SAMPLER too). To update another app, deploy it while Forth is running (`--no-reload`), then `BYE` and start it from the chooser.
- `RELOAD` checks the new file's header and CRC first. A broken build is refused, and the old app keeps running.

Utilities don't need this loop: copy the new `.BIN` into `R8UTILS` and run it again from RUN UTIL.

## 9. Going back to stock

Repeat step 6 with Zoom's own `R8SYS.BIN` in the card root. Holding PLAY at power-on is handled by the boot loader, before the main firmware runs, so this works even if a launcher or patch misbehaves ([section 2](02-safety-and-recovery.md#the-recovery-path)).

## Where next

- [Section 7](07-building-custom-firmware.md): the four ways to run your own code, and the rules for each.
- [Using the apps and utilities](apps-guide.md): what each app and utility does, and its controls.
- [Section 8](08-forth-app.md): the Forth app, its words, and how to add C words for your own experiments.
- [Section 10](10-tools.md): the simulator and tests. Run a test before trying a change on the R8.
