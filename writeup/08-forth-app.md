# 8. The Forth app

The Forth app is the reference custom firmware from this project: an app loaded from the SD card by the app-launcher firmware. It gives an interactive Forth prompt over USB serial and exposes the SD card as a USB drive. It comes as two apps built on one core (since 13 November 2026):

- **Forth** (`R8APPS/FORTH.BIN`, source `cbuild/forth/`): the core alone, for ad hoc programs. Add a C word in `forth/main.c` with `forth_word()`, or type Forth at the prompt.
- **Sampler** (`R8APPS/SAMPLER.BIN`, source `cbuild/sampler/`): the core plus a four-track variable-speed looper with recording. It starts the looper at boot; the Forth prompt and the core words stay available.

The core (`forth/core.c`, `core.h`) holds the console, USB, storage, the power switch and battery shutdown, and the general words. An app passes the core a small set of hooks (`struct core_app`): whether it owns the screen, panel and audio right now, a poll function, its screen, stop (before power-off, `RELOAD` or `BYE`), sample-rate change and card release.

## Architecture

| File | Role |
| --- | --- |
| `entry.asm`, `l2.cmd` | Entry point and link map, shared by both apps: L2 (0x11800000–0x1182C000) for everything not marked otherwise, and the app DDR window (0xC0000000–0xC00FFFFF) for `.ddrtext`, `.ddrdata` and `.ddrbss` (section 7) |
| `core.c`, `core.h` | Start-up, the main loop, the LCD console, the core words, the app hooks |
| `main.c` | The plain Forth app: the core with no hooks, and the place for ad hoc words |
| `forth.c` | A small Forth: threaded code (32 KB), data space (16 KB), integer words, compiling words, `DO … LOOP`, `BEGIN … UNTIL` |
| `usb.c`, `msc.c` | USB0 device driver: CDC-ACM serial plus bulk-only mass storage |
| `sd.c`, `fat.c` | SD card driver (polled; multi-block runs of up to 128 blocks with DMATRIG; optional EDMA reads) and FAT32 (read files, create new ones) |
| `../sampler/main.c` | The sampler app: looper words, looper screen, hooks; starts the looper at boot |
| `../sampler/looper.c`, `../sampler/vectors.asm`, `../looper/voice.c` | The looper: audio interrupt and its vector table, voices, panel handling, recording, saving |
| `clock.c` | The audio master clock (board ID, I2C clock chip or oscillator pins) |
| `aio.c` | Simple polled audio pass-through (`PASS`) |
| `reload.c`, `tramp.asm` | `RELOAD`: load the app's own file again (a new build) over the running app |
| `../synth/hw.c`, `lcd.c`, `panel.c` | Shared low-level drivers: power, pin mux, LCD over SPI1, the panel link |

The main loop services USB (serial and storage), the power switch and the app's background work (the sampler's file loads). In the sampler, audio runs from timer 1's interrupt at 8 kHz, which also reads the panel UART, so faders and keys keep working while the main loop is busy with USB or the card.

## How the Forth works

`forth.c` is a small Forth written in C. It runs on the R8's only processor, the TI C6748 DSP; there is no ARM core on this chip.

**Stacks.** The data stack and the return stack are the Forth's own: two C arrays of 256 32-bit cells (`ds[]`, `rs[]`), each with its own index. Overflow and underflow are checked and reported ("stack full", "stack empty") instead of corrupting memory. The DSP's C stack (B15) is used only by the C code: the interpreter loop, the primitives and the C words. Calling a Forth word from another does not use the C stack, so deep Forth nesting cannot overflow it.

**Threading.** It is token-threaded:

- A colon definition compiles to an array of dictionary indices in a 32 KB code area. Each cell is the number of a word, not an address. Literals and branch targets follow their opcode inline.
- The inner interpreter (`execute()`) fetches the next index and dispatches on the word's kind:
  - **colon word**: push the current position on the return stack and continue in its body (`EXIT` pops it);
  - **primitive** (`DUP`, `+`, `@`, `BRANCH`, `DO … LOOP` and so on): one C `switch`;
  - **C word**: a call through a function-pointer table.
- The outer interpreter reads a line, looks up each word (case-insensitive), and runs it or compiles it. Anything else is parsed as a number: `123`, `-5`, `$1F`, `0x1F`, `#10`, `%101`.

**C words.** Everything R8-specific is a C word registered with `forth_word(name, fn)`: the SD, panel, LCD and audio words in `core.c`, and the looper words in the sampler. A C word takes its arguments with `forth_pop()`, checks `forth_failed()` (stack underflow), and returns results with `forth_push()`. For example:

```c
static void w_twice(void)                    /* ( n -- 2n ) */
{
    int n = forth_pop();
    if (forth_failed()) return;
    forth_push(2 * n);
}
/* in main(), after core_init(): */
forth_word("TWICE", w_twice);
```

**Limits.**

- 512 dictionary entries, 32 KB of compiled code, 16 KB of data space (`HERE`, `ALLOT`, `VARIABLE`, `CREATE`), 64 C words.
- Cells are 32 bits and arithmetic is integer only.
- The dictionary, the code area and the data space are in the app DDR window (`.ddrbss`, section 7); the stacks and the interpreter are in L2. `HERE` and `VARIABLE` addresses are therefore 0xC000xxxx, which `.` prints as negative numbers; `U.` and `HEX` show them plainly.
- `@`, `!`, `C@`, `W@` and the like reach any address, so a line of Forth can read or write hardware registers directly.
- Nothing persists: `RELOAD`, `BYE` or switching off starts again with an empty user dictionary.

**Running with the rest of the app.** The Forth is single-threaded and shares the main loop with USB, the LCD and the power switch:

- The main loop services them between lines.
- While Forth code runs, the inner interpreter calls `forth_poll()` every 1,024 words, and `MS` and `KEY` poll while they wait. So USB, the screen and the power switch keep going during long Forth loops.
- Ctrl-C sets an abort flag that is checked at those same points.
- C words are not interrupted: a long-running one must call `forth_poll()` (or `core_service()`) itself.
- Interrupt-driven work carries on regardless. In the sampler, the looper's timer interrupt plays audio and reads the panel while a word runs.

## Starting it: the app chooser

RUN APP (TOOL → SD CARD) starts `A:\APP.BIN`, which is now a small app chooser (`cbuild/chooser`). It lists the `.BIN` files in `A:\R8APPS` by name: UP/DOWN (or the wheel) move the selection, LEFT/RIGHT move a page, ENTER runs the app, EXIT restarts into the normal R8 firmware. A file that isn't a valid app (bad header or CRC) is refused with the reason on the bottom line. The Forth app lives there as `R8APPS/FORTH.BIN`.

The chooser leaves the path of the app it started at a fixed DDR address (`0xC03FFF00`: magic `R8LP`, then the path), so an app can find itself: the Forth app's `RELOAD` reloads that file. `BYE` restarts into the stock firmware with the same watchdog reset the chooser's EXIT uses (`r8_restart()` in `synth/hw.c`).

## Connecting

The app enumerates as "R8 Forth". On a Mac the serial port is `/dev/cu.usbmodemR8F11`; open it with `screen /dev/cu.usbmodemR8F11`. The LCD shows the USB state and the last lines of the console. Ctrl-C stops a running word.

**USB storage is off by default**: the computer sees no card, so nothing mounts and the R8 can write to the card freely. To copy files, turn it on:

- Press the **USB** key: the card appears read-only (the computer mounts it within a few seconds). Press it again to turn storage off; this is refused once the computer has started using the card ("eject the card on the computer first").
- Or from Forth: `MSC-RO` (read-only) or `MSC-RW` (writable), and `MSC-OFF`.
- Ejecting the card on the computer also turns storage off. `SAVE` refuses while the computer has the card.

## Words

Standard words include `: ; IF ELSE THEN DO LOOP +LOOP I J BEGIN UNTIL WHILE REPEAT AGAIN VARIABLE CONSTANT CREATE ALLOT , C, @ ! C@ C! W@ W! DUMP . U. .X .S HEX DECIMAL WORDS FORGET TICKS MS`, and the usual arithmetic and stack words. `TICKS/MS` gives the timestamp counter's rate.

**Card and USB**

| Word | Stack | What it does |
| --- | --- | --- |
| `SD-INIT` | ( -- err ) | initialise the card |
| `SD-INFO`, `SD-BLOCKS` | | card identity and size |
| `SD-READ` | ( block addr -- err ) | read one block |
| `SD-READN` | ( block count addr -- err ) | read several blocks (runs of up to 128) |
| `SD-READD` | ( block count addr -- err ) | the same with EDMA (experimental) |
| `SD-WRITE` | ( block addr -- err ) | write one block. **No range check**: it will write over the filesystem if you ask it to |
| `SD-WRITES` | ( block addr n -- err ) | write n blocks (runs of up to 128). **No range check either** |
| `SD-BUSY` | ( block blocks runs -- ) | write `runs` bursts of `blocks` (1–128) blocks at block and report the card's programming time after each write (average and worst), throughput and the slowest write; only before the first partition, never with USB storage on. Run it under an app's real load to size buffers |
| `SD-TEST` | ( block n seed -- bad ) | write n (1–128) blocks of a seeded pattern, read them back one at a time, compare; only blocks 1–8191, before the usual partition start. -1 = all match, else the first differing byte (-2 refused, -3 read error, -4 write error) |
| `MSC-RW`, `MSC-RO` | | turn USB storage on, writable / read-only (reconnects) |
| `MSC-OFF` | | turn USB storage off (refused while the computer is using the card) |
| `MSC-LOG` | | the last 32 SCSI commands from the host |
| `RELOAD` | | load and run this app's file again (the one the chooser started; else `A:\APP.BIN`) |
| `BYE` | | restart into the stock R8 firmware |

**SD experiments** (plain Forth app only, `cbuild/forth/main.c`; how the 4 KB fault was found, section 6)

| Word | Stack | What it does |
| --- | --- | --- |
| `SD-RCMP` | ( block n mode -- bad ) | read n (2–128) blocks one at a time, then with one CMD18 using the `SD-FLAGS` flags (mode 0 CPU, 1 EDMA), print both times, compare; read-only |
| `SD-WCMP` | ( block n seed -- bad ) | one CMD25 of n (2–128) blocks with the `SD-FLAGS` flags, read back one at a time, compare; only before the first partition (read from the MBR) and below 8192 |
| `SD-FLAGS` | ( mask -- ) | MMCCMD flags for those two words: any of `80` (PPLEN), `8000` (DCLR), `10000` (DMATRIG, what the driver uses); the default is `8080`, the old flags, so the fault shows |
| `SD-CLK` | ( clkrt -- ) | card clock 90 MHz / (clkrt + 1), 3–255; `SD-INIT` puts it back to 3 (22.5 MHz) |
| `FSEEK-TEST` | ( -- ) | reads `A:\R8SYS.BIN` through `fat_map`: extents, a sequential pass with its rate and CRC-32 (compare with the computer's), and 50 random unaligned `fat_read_at` reads checked against it, with the slowest |

To time the driver itself, wrap a word in `TICKS` (360 MHz), for example `HEX : TRD TICKS >R C0100000 SD-READN TICKS R> - ; DECIMAL`, then `100000 2048 TRD 360 / . .` prints microseconds and the error code.

**Audio and clock**

| Word | Stack | What it does |
| --- | --- | --- |
| `AUDIO-INIT`, `PASS` | ( ms gain -- ) | polled input-to-output pass-through, with peaks and error counts |
| `RATE` | ( hz -- ) | set the output rate, 44100 or 48000; loops keep their pitch |
| `.RATE` | | show the rate and board ID |
| `CLK-RAW` | ( N F -- ) | write a raw clock-chip setting (board 7; for experiments) |

**Looper** (sampler app only)

| Word | Stack | What it does |
| --- | --- | --- |
| `LOOPER` | | load the first four WAVs from `ZOOM_R8/LOOP` and start |
| `LOOPER-STOP` | | fade out and stop |
| `LOOPS` | | status of each track, CPU use, underruns |
| `LOOPFILES` | | the WAV files in the folder, and which track plays each |
| `RESCAN` | | re-read the folder (also done automatically when the computer ejects the card) |
| `LOAD-LOOP` | ( file track -- ) | load a file into a track |
| `FADER!` | ( value fader -- ) | act as if a fader moved (fader 9 = MASTER) |
| `LOOP-RESTART` | | restart every loop from its start (as STOP does) |
| `MONITOR` | ( 0-127 -- ) | input level in the output (default 0) |
| `REC` | ( track -- ) | record the inputs into a track |
| `REC-MIX` | ( track -- ) | record the mix of the other tracks (at their levels, before MASTER, without the input) into a track |
| `REC-STOP` | | stop recording; the track loops the recording |
| `SAVE` | ( track -- ) | save a track's recording as `ZOOM_R8/LOOP/RECnnnnn.WAV` |
| `INPUT` | | input peaks and counters |

**Panel**

| Word | Stack | What it does |
| --- | --- | --- |
| `PANEL-MON` | ( seconds -- ) | print every panel message, decoded |
| `PANEL-TX` | ( addr n -- ) | send raw bytes to the panel (stock commands only) |
| `FADERS` | | read every fader position from the panel |
| `LCD-VOL` | ( 0..63 -- ) | LCD contrast, raw (`81 vv`; 11–21 usable on our unit) |
| `CONTRAST` | ( 1..8 -- ) | LCD contrast as stock's setting (5 = default = `81 12`) |
| `DDR-INFO` | | the app DDR window, where `.ddrbss` lies, and a check that the loader placed `.ddrdata` (section 7) |

## Using the looper from the panel (sampler app)

| Control | Function |
| --- | --- |
| Faders 1, 3, 5, 7 | Speed of tracks 1–4: middle = stopped, top = +4×, bottom = −4× (reverse); flat spots at exactly +1× and −1× |
| Faders 2, 4, 6, 8 | Level of tracks 1–4, on the R8's printed scale: about 3/4 up (position 100) = 0 dB, top = +12 dB |
| MASTER | Output level, same scale |
| STOP | Restart every track from its beginning |
| Hold a key under the display + turn the wheel | Pick a WAV for that track (it is silent while held); release to load it |
| REC | Arm recording (REC LED blinks); press again to disarm |
| Armed, then a key under the display | Record the inputs into that track (REC LED on) |
| The same key again, or REC | Stop recording; the track loops what was recorded |
| Either status key above a track's faders | Record the mix of the other tracks into that track (both keys light red); press again to stop, and the track loops it |
| USB | Turn USB storage on (read-only) or off |

The LCD shows two rows per track: the file name, then the speed (for example `+1.00x`) and a level bar. A recording track shows `REC` and the seconds recorded.

Loops can be 16-, 24- or 32-bit integer or 32-bit float WAVs (including WAVE_FORMAT_EXTENSIBLE), mono or stereo, at any sample rate; if a file can't be loaded, the track shows why (for example `too big`); each track holds up to 15 MB of file. Recordings are 16-bit stereo at the current rate, up to about 89 seconds at 44.1 kHz.

## Not yet done

In the sampler: anti-aliasing at speeds above 1×; monitoring from the panel; saving from the panel; trimming recordings; a panel key to leave the app (today `BYE` or the power switch); using the pads. In the core: USB high speed.
