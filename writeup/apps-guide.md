# Using the apps and utilities

How to use what this repository builds, once it is on your card ([Getting started](getting-started.md)). The other sections explain how these programs work; this one is about playing with them.

## What all apps have in common

- **Starting:** TOOL → SD CARD → RUN APP opens the chooser; pick an app and press ENTER.
- **Leaving:** EXIT in Blippoo, PADTEST and POWERLOG (and in the chooser) restarts the R8 into its normal firmware. The Forth app and the sampler have no panel key for this: type `BYE` on the computer, or switch the R8 off.
- **The power switch** works in every app.
- **Batteries:** the apps read stock's BATTERY setting and follow its cut-off. When the battery is empty they say so and switch off. Blippoo first saves a recording in progress.
- **Files:** the apps save by creating new files; they never change or delete existing ones. (The Forth app's raw block words, `SD-WRITE` and `SD-WRITES`, are the exception: they write wherever you tell them.) Wait for a save to finish before switching off.

## The chooser (`APP.BIN`)

Lists the `.BIN` files in `A:\R8APPS` by file name, six to a screen.

| Control | Function |
| --- | --- |
| UP / DOWN, or the wheel | Move the selection |
| LEFT / RIGHT | Move a page |
| ENTER | Run the selected app |
| EXIT | Restart into the normal R8 firmware |

A file that isn't a valid app (wrong header, or the checksum doesn't match) is refused, with the reason on the bottom line, and the chooser keeps running.

## FORTH

An interactive Forth over USB serial, with words for the card, the panel, the LCD and audio. It is the place for ad hoc experiments: add a C word in `cbuild/forth/main.c`, or type a definition at the prompt. [Section 8](08-forth-app.md) is its manual.

## SAMPLER

Four loops whose speed and level are under the faders, plus recording from the inputs. It starts playing straight away: it loads the first four WAV files in `ZOOM_R8/LOOP`, in file-name order. The Forth prompt stays available over USB, with extra words for the looper.

| Control | Function |
| --- | --- |
| Faders 1, 3, 5, 7 | Speed of loops 1–4: middle = stopped, top = 4× forwards, bottom = 4× backwards; flat spots at exactly 1× each way |
| Faders 2, 4, 6, 8 | Level of loops 1–4 (about three-quarters up is 0 dB) |
| MASTER | Output level |
| STOP | Restart every loop from its beginning |
| Hold a key under the screen (loops 1–4) and turn the wheel | Choose a WAV for that loop; release the key to load it |
| REC, then a key under the screen | Record the inputs into that loop; the same key (or REC) stops, and the loop plays the recording |
| Either status key above a loop's faders | Record the mix of the other loops into that loop (both keys light red); press again to stop |
| USB | Show the card to the computer (read-only), or stop showing it |

To put loops on the card, copy WAVs into `ZOOM_R8/LOOP`. Most WAV formats load: 16, 24 or 32 bits, float, mono or stereo, any sample rate, up to 15 MB each. Recordings are 16-bit stereo, up to about 89 seconds at 44.1 kHz. There is no save from the panel yet: from the computer, `SAVE` ( track -- ) writes a loop's recording as `ZOOM_R8/LOOP/RECnnnnn.WAV`. [Section 8](08-forth-app.md#using-the-looper-from-the-panel-sampler-app) has the details and the looper's Forth words.

## BLIPPOO

An emulation of Rob Hordijk's Blippoo Box: two chaotic oscillators that modulate each other, two runglers (small shift-register sequencers), and a "twin peak" resonator that rings when the oscillators' triangles cross. It is based on Hordijk's article in Leonardo Music Journal 19 (2009). The sound goes to the line out and the headphones; the inputs can be fed into the resonator.

The eight channel faders are the knobs, in two banks. The keys under the screen choose the bank, and MASTER is the output level in both.

| Fader | Bank 1, CORE | Bank 2, RESONATOR |
| --- | --- | --- |
| 1 | OSC A: oscillator A's frequency (about 20 octaves, down to almost stopped) | PEAK 1: the resonator's first peak |
| 2 | OSC B: oscillator B's frequency | PEAK 2: the second peak |
| 3 | SWP A: how much B's triangle sweeps A | RNG>PK: how much the runglers move the peaks |
| 4 | SWP B: how much A's triangle sweeps B | SPREAD: the sample-and-hold moving the peaks apart |
| 5 | RUNG A: rungler A's modulation of A | EXT IN: the inputs (1 and 2) into the resonator |
| 6 | RUNG B: rungler B's modulation of B | PING: the crossing pulses into the resonator |
| 7 | WARP: the sample-and-hold's modulation of both oscillators ("time warp") | (unused) |
| 8 | COLOUR: the resonator's self-modulation (harmonics without clipping) | (unused) |

**Pickup.** A fader only takes over a parameter once it reaches the parameter's current value. Until then the screen shows an arrow (`^` or `v`) next to the value, pointing the way to move the fader. So switching banks never makes a sound jump. At start-up bank 1 takes the faders' positions and bank 2 starts from its defaults.

**Screen.** The title row shows the bank, then four rows of two parameters with their values (0–99). Below them the recording status, the crossing pulses per second, and the CPU load.

**Recording.** REC starts recording what you hear (after MASTER) as 24-bit stereo at 44.1 kHz; the REC light is on and the screen counts the time and the time left. REC again stops and saves a new file `ZOOM_R8/LOOP/BLIPnnnn.WAV` (in the card's root if there is no `LOOP` folder). The sound keeps playing while it saves. A take can last up to about 4 minutes on a card with 32 KB clusters (about 2 minutes with 4 KB clusters); recording stops and saves by itself when it's full. EXIT while recording saves first, then restarts. A 60 s take saves in about 5 seconds.

## PADTEST

Shows what the panel sends, for exploring the protocol. The top row shows the pads held now. Below it is a running list, newest first:
- pad hits with their velocity, and releases with how long the pad was held;
- keys pressed and released, by name;
- wheel turns;
- any message of a kind it doesn't know, in hex.

Faders and the regular power readings are left out of the list.

Everything the panel sends is logged with a timestamp. **EXIT twice** within 2 seconds saves the log as `PADnnnn.TXT` in the card's root and restarts; a single EXIT only shows up in the list.

## POWERLOG

Logs what the R8 reports about its power supply, for working out battery behaviour. It records:
- the panel's supply readings (`C1`, `C2`);
- every change on the DSP's input pins;
- a snapshot of all input pins every 10 seconds;
- marks: the four keys under the screen write MARK 1 to MARK 4, so you can label each step of a test ("batteries in", "USB out", ...).

The screen shows the latest readings and a running list. **EXIT twice** within 2 seconds saves the log as `PWRnnnn.TXT` in the card's root and restarts. Keep the R8 powered while it saves.

## Utilities (RUN UTIL)

TOOL → SD CARD → RUN UTIL lists the `.BIN` files in `A:\R8UTILS` as a normal R8 menu. A utility runs inside the stock firmware, uses its menus, and returns to the list. EXIT goes back to SD CARD.

| Utility | What it does |
| --- | --- |
| FILEINFO | Browse the card and show a file's size, dates and attributes; for a WAV also its rate, bit depth, channels and length |
| VARISPD | Make a sped-up, slowed-down or reversed copy of a WAV. Pick a file, set SPEED with the wheel (−4.0× to +4.0× in 0.1 steps; negative plays backwards), and ENTER writes a new file next to it, for example `Bike loop +1.5x.wav`. The original is never changed |
| REAMP | Re-amp a track through an external effect: the track plays on the line out, the inputs record the effect's return as 24-bit stereo, and the headphones carry the return. Recording continues after the track for tails, until STOP. The result, `<name> REAMP.wav`, is lined up with the source |
| BATTINFO | Show stock's battery readings live: the BATTERY setting, the supply reading and its voltage, USB or battery power, the level |
| TONETEST | Plays test tones on the line out and the headphones for 3 seconds and records the inputs to `A:\TONETEST.WAV` (a hardware experiment) |
| KEYLOG | Logs the stock UI's key codes to `A:\KEYLOG.DAT` (a reverse-engineering aid) |
| LISTDIAG | Saves the raw directory listings the stock file API returns (a debugging aid) |
| HELLO | Shows a popup and returns: the smallest possible utility, as a template |
