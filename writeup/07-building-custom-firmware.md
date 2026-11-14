# 7. Building custom firmware

There are four ways to get your own code running on the R8. Two keep Zoom's firmware running and add to it; two replace it. We used all four.

| Way | What runs | Delivered as | Turnaround | Good for |
| --- | --- | --- | --- | --- |
| 1. Patch stock firmware | Zoom's firmware plus your hooks | a modified R8SYS.BIN | minutes (reflash) | logging, small features inside the stock UI |
| 2. Utility from the card | Zoom's firmware, calling your code from its menu | a `.BIN` in `A:\R8UTILS` (after a one-time patch) | seconds (copy a file) | tools that use the stock UI, file system and audio, then return to the menu |
| 3. Standalone firmware | only your code, after the boot loader | your own R8SYS.BIN | minutes (reflash) | a complete replacement |
| 4. App from the card | your code, loaded by a launcher firmware | a `.BIN` in `A:\R8APPS` | seconds (`RELOAD`) | development, and anything that needs the whole machine |

All four are reversible with the PLAY recovery (section 2). Utilities and apps never touch the flash, so a broken one only needs a power cycle.

## 1. Patching stock firmware

You keep everything Zoom's firmware does and add to it. Our patches were a panel-link logger, a flash dumper, an LCD fader display, a register dumper, a REVERSE item in the TRACK EDIT menu, and the RUN APP and RUN UTIL items that start ways 2 and 4.

**Hooking a call.** Find a `CALLP` to the function you want to intercept and change only its displacement field, so it calls your code instead. The C674x sets up arguments in the same execute packet as the call, so the hook receives exactly what the original would have. A hook that wants to stay transparent calls the original with the same arguments and returns its result, preserving the callee-saved registers.

**Where the code goes.** See section 4: unused SYS/BIOS name strings for small hooks, and the file's 0xFF padding (about 55 KB) for larger code. Code in the padding is flashed but not loaded, so a small loader in the hook copies it to RAM with the firmware's flash-read routine, writes back and invalidates the caches, and calls it.

**Calling the firmware from your code.** The stock code is about 7 MB away from L2 and the padding area, beyond `CALLP` range, so use far branches. TI's linker inserts these trampolines automatically when you link against a file of stock function addresses.

**Writing the code.** We started with a small hand assembler that only emits encodings found in the firmware itself, then moved to C with TI's compiler (`cl6x -mv6740`) and no runtime library. In C, `r8.h` declares the stock functions and `r8_stock.cmd` gives their addresses.

**Fixing the checksum.** After any change, recompute the byte sum at offset 0x34 (`r8fw.py fixsum`).

**The launcher patch.** RUN APP and RUN UTIL come from one patch: `cbuild/launcher` is compiled with `cl6x`, and `tools/utilmenu.py` writes it, with the menu changes and REVERSE, into a copy of your own stock `R8SYS.BIN` (it refuses anything but 1.17). [Getting started](getting-started.md) gives the commands. (`tools/appmenu.py` is the earlier version with RUN APP only.)

**Adding an item to a stock menu.** Menus are data (section 4), so a new item is a new record and a longer list. The TOOL → SD CARD list has room for only four items (EXCHANGE, FORMAT, REMAIN, then RUN APP in our build), and the next menu's list follows it in memory. To add RUN UTIL as a fifth, the patch builds a new six-word list in the code cave (five record pointers and a terminating 0) and points the SD CARD descriptor (`0xC06DFFA8`, list at +32) at it with the count set to 5. RUN UTIL reuses RUN APP's handler with a different argument. Confirmed on hardware.

**Pitfalls we hit:**

- State kept in DDR that the boot loader had used was not zero at start-up (section 6).
- The stock file API refused to delete a file the recorder had attached; the REVERSE feature had to detach it first, as Trim does.
- Menus and dialogs have conventions (closing "Please Wait…", item types that draw icons) that only the stock code shows. Copy what an existing feature does.

## 2. Utilities: code from the card that runs inside the stock firmware

A patch has to be reflashed for every change. A utility is loaded from the card each time it runs, is called as a function from the stock menu, uses the stock firmware's file API, dialogs, menus and audio, and returns to the menu. The R8 keeps working as a recorder around it. All of this is confirmed on hardware (2026-11-12 and 13).

**How it starts.** The launcher firmware (way 1) adds RUN UTIL to TOOL → SD CARD. It loads `A:\R8UTILS\UTILMENU.SYS` to its link address in DDR the stock firmware leaves idle (0xC0100000–0xC0240000, the boot loader's old buffer), checks the header and CRC, writes back the data cache and invalidates the program cache, and calls it. `UTILMENU.SYS` lists the `.BIN` files in `A:\R8UTILS` as a stock menu titled R8 UTILS and starts the one picked the same way, at 0xC0110000. Adding a utility means copying a file to the card; the firmware doesn't change.

**Format.** The same 32-byte header as an app (way 4) with magic `R8UT`, built by `mkutil.py`. The entry point is called with the utility's own path and returns when done.

**Rules for utility code:**

- It runs on the menu task's stack. Keep stack use small and put buffers in static memory.
- It must not take over hardware the stock firmware is using. Use the stock functions for files, popups and dialogs, and join the audio interrupt through its hook (below) rather than programming the McASP.
- Uninitialised data is not zeroed by the loader; our runtime clears it on entry.
- Stock calls are far calls, as in way 1.

**Using stock menus.** A utility builds menus in the stock format and runs them with the stock menu engine (`menu_engine(descriptor, 0, 0, 0)`, the way TOOL opens SD CARD). Ours copy the record layout of FORMAT's item and use SD CARD's event callback. What we learned about the engine (from firmware, then used on hardware):

- **Descriptor:** +0 item count, +4 title, +20 and +24 pointers to two state words (the first is the cursor), +28 0x10013, +32 the item list (record pointers, 0-terminated), +36 the event callback. The engine starts with the cursor at the first state word and re-reads the count each time round its loop, so a utility can rebuild the list in place (our file picker does, after writing a file).
- **Records** (20 words): +0 the label, +16 the ENTER handler. Before calling the handler, the engine stores the picked index in the first state word, so one handler can serve every row. Words 12–15 are the soft-key labels at the bottom of the screen.
- **Two columns.** The menu screen always draws a divider at x = 60. The label is drawn at x = 0 (about 8 characters of 7 px) and a value at x = 62. The value comes from record +32, a function the engine calls with a structure whose word 3 is record +28; it returns a string. Unselected values are cut to 11 characters; a selected value longer than 10 scrolls (but see the scroller bug in section 6).
- **Wheel-edited values.** If record +28 points to an integer, the wheel changes it within record +36 to +40 (signed) and then calls a hook at record +44. Record +24 bit 4 makes it wrap, bit 5 calls the hook even when the value didn't change, and **bit 3 makes the row read-only**. Set bit 3 on any row whose +28 is not an integer: otherwise a wheel turn writes to whatever it points at.
- **Nesting works.** TOOL → SD CARD → R8 UTILS → a utility's own menus (a file picker nests one menu per folder level).

**Keys and the wheel.** `ui_event(timeout)` (`0xC07B5B58`) waits for the next UI event and returns 0 on timeout. A key posts `0x10000 | ID` on press and `0x20000 | ID` on release (appendix A); the wheel is `0x10000000`. Checked on hardware for ENTER, EXIT and STOP.

**Playing and recording.** A utility can replace the per-block pointer at `0x11F02E74` in the stock audio interrupt, read the input frames and write the output frames of each 8-frame block, and put the pointer back before returning (section 5). The line out and headphones can carry different signals. This runs while a stock menu or dialog is on screen.

**Writing new files.** The stock create call takes long names, so a utility can write `<source name> +10%.wav` next to its source. Keep a fallback: if the create fails, ours make an 8.3 name and show it in the "SAVED" popup.

**Examples** (`cbuild/utils`):

- **FILEINFO:** a file picker built from stock menus (folders first, long names in the value column); shows size, dates and attributes, and for a WAV its rate, bit depth, channels and length.
- **VARISPD:** varispeed a WAV into a new file. SPEED is a wheel-edited row from −4.0× to +4.0× (negative plays backwards), with a windowed-sinc resampler.
- **TONETEST:** plays different tones on the line out and headphones and records the inputs; this is how the outputs and the 47-sample round trip were found.
- **REAMP:** plays a WAV on the line out, records the inputs as 24-bit stereo, sends the input to the headphones, keeps recording after the track ends until STOP, and aligns the result by the round trip.
- **BATTINFO:** stock's battery readings as live label/value rows (section 5).
- **HELLO:** a popup and nothing else, the smallest utility; a starting point for your own.

## 3. Standalone firmware

Only your code runs after the boot loader. Build an R8SYS.BIN with:

1. The stock 64-byte header (version and tag), with the checksum fixed.
2. An AIS script: the stock SeqReadEnable and the stock PLL and DDR2 commands byte for byte, then section loads for your program, then a jump to your entry point.

Our `standalone_c.py` does this for a C program linked at 0x11800000 (L2).

Your code then has to bring up everything itself:

- Interrupts off, a stack in L2.
- Power domains: PSC1 modules for GPIO (3), SPI1 (10), UART2 (13), McASP0 (7), MMCSD1 (18), USB0 (1) as needed.
- Pin multiplexing: the stock values, except PINMUX4 (the panel UART), which the boot loader already set.
- The LCD (SPI1 and the init sequence, section 5) and the panel link (UART2 and the start-up messages, appendix A).
- The power switch: poll GP8[12] and clear GP7[0] to turn off. Without this the unit can't be switched off.

Our first standalone builds were a test pattern on the LCD, then a fader display, then an eight-voice synth on the audio output.

## 4. Apps loaded from the card

A launcher firmware (stock plus a RUN APP menu item) loads `A:\APP.BIN` into L2 and jumps to it, after taking over the machine from the stock firmware. The app format (`mkapp.py`) is a 32-byte header: magic `R8AP`, version, load address, length, entry point, CRC-32 of the image, and the top of its memory. An app must fit 0x11800000–0x1182C000 (image plus uninitialised data); the stack sits above. Large buffers can go in DDR, which the app owns once it has taken over.

Once an app has its own USB driver (ours exposes the card as a USB drive), the development loop no longer needs the card to leave the R8:

1. Re-connect the card as writable (`MSC-RW`).
2. Copy the new build over the app's file (for example `R8APPS/FORTH.BIN`) and check its checksum.
3. Eject on the computer, and confirm the eject.
4. `RELOAD`: the running app checks the new file's header and CRC, then copies it over itself from a small trampoline in DDR (`reload.c`, `tramp.asm`), synchronises the caches and jumps to it.

The Forth app (section 8) is the example; [Getting started](getting-started.md) shows the loop with `tools/fs.py`.

**Several apps:** the app chooser (`cbuild/chooser`) runs as `APP.BIN` and starts any app in `A:\R8APPS`. It hands the app its own path (section 8), so `RELOAD` keeps working.

**Exiting an app:** an app takes over the whole machine, overwriting stock code and its interrupt table in L2, so it can't return to the running stock firmware. It can restart into it: Timer64P1 armed as a watchdog and never serviced resets the chip exactly like power-on (`r8_restart()`, section 5). If you need to return to the stock menu without a restart, write a utility (way 2) instead.

**Text on the screen.** An app draws its own text. Ours use a 5×7 font of our own (`cbuild/forth/font.txt`, converted by `tools/mkfont.py`), so the binaries contain no Zoom data. Building with `FONT=stock` uses the stock font from your own firmware instead (`cbuild/font.sh`); keep such binaries to yourself.

**Writing files from an app.** An app has no stock file API; it needs its own SD and FAT code. Ours (`cbuild/forth/sd.c` and `fat.c`) only creates new files, from free clusters and an unused directory slot, writing data, then the FAT chain, then the directory entry, so an interrupted save cannot damage existing files. Check the result with a real disk checker. Never write while a computer has the card mounted over USB (section 2).

**Other examples** (each in `cbuild/`):

- **BLIPPOO:** an emulation of Rob Hordijk's Blippoo Box (two chaotic oscillators, runglers and a twin-peak resonator) at 4× oversampling, about 35% of the CPU. The faders are the knobs, in two banks switched by the keys under the display, with pickup so a bank switch doesn't jump values. The inputs feed the resonator. REC records the output as 24-bit stereo into DDR (room for about 4 minutes, though our FAT code caps one file at 8192 clusters, about 2 minutes with 4 KB clusters) and saves it as a new WAV in `ZOOM_R8/LOOP`. The sound, screen and power switch keep working during the save; key presses made then are dropped. Confirmed on hardware 2026-11-13.
- **SAMPLER:** the Forth core plus a four-loop varispeed looper with recording, controlled from the faders and keys (section 8). It shows how an app adds to the core through its hooks.
- **PADTEST:** shows pad, key and wheel events and any unknown panel message, and on EXIT pressed twice saves every received panel byte with a timestamp to `PADnnnn.TXT`. It settled the pad protocol and found REPEAT/STOP (appendix A).
- **POWERLOG:** logs the panel's supply readings and every DSP input-pin change, with marks from the keys, to `PWRnnnn.TXT`; it found the USB-power pin and the PHANTOM switch (section 5).

[Using the apps and utilities](apps-guide.md) describes each from the user's side.

## Testing before hardware

Whatever the way, we ran each build in our simulator against models of the board before putting it on the R8 (section 9 for the method, section 10 for running the tests with `tools/run_tests.py`). Most hardware runs then answered real questions rather than finding typos. For utilities, the tests replace the stock menu engine, file API and audio interrupt with scripted models (`test_fileinfo.py`, `test_reamp.py`); the menu patch itself is tested against a snapshot of the real stock menus (`test_utilmenu.py`).
