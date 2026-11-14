# 11. Timeline: steps and decisions

A short record of the order things happened in and why, for anyone planning similar work. Dates are 2026. Most of the time went into the dead ends and fixes noted along the way, not into the steps that worked first time.

## 6 November: understanding the firmware without touching the unit

- **Read the update file first.** R8SYS.BIN turned out to be a 0x40-byte Zoom header in front of a standard TI AIS boot script, protected only by a byte sum. That identified the processor family (C674x DSP, C6748 class), the memory map and the load addresses before any hardware work.
- **Built a static-analysis kit** in plain Python: section extraction, a C674x decoder, cross-references, call graphs, string references. The decoder covered only part of the instruction set; that limit caused mistakes until we started using TI's own disassembler (8 November).
- **Mapped the update path and the boot loader** before flashing anything. The first experiment was a same-length string edit plus a fixed checksum, to check the updater accepts modified files. Then a patch that dumps the whole 8 MB flash to the card. The dump showed R8BOOT has its own updater, and a test confirmed that holding PLAY at power-on reaches it without running the main firmware. **Decision: no standalone or risky experiments until this recovery path was confirmed.**
- **Captured the panel link with a patched firmware instead of a logic analyzer.** The original plan was to open the unit and probe the serial link to the panel processor. Instead, a patch hooked the firmware's own UART read and write calls and logged both directions to a file. This found the panel protocol (keys, faders, pads, wheel) without opening the case.
- **Found the LCD is driven directly by the DSP over SPI**, not through the panel processor, by static analysis. Two patched builds (a fader bar, then a full-screen fader display) confirmed the frame buffer format on the unit.
- **First standalone firmware**: a test pattern on the LCD with no Zoom code running after the boot loader, then a standalone fader display with our own UART and LCD drivers.
- **A feature in the stock firmware**: REVERSE as a TRACK EDIT menu item, built as flash-loaded code called from a menu hook. It took several builds: the failures were a missing "detach file" step, a dialog left on screen, and a menu icon.

## 7 November: a C toolchain, a simulator, and apps from the card

- **Installed TI's C6000 compiler** and set up a runtime-free build, with far-call trampolines into the stock firmware for patches.
- **Wrote a C674x simulator** (`sim674`) able to run compiler output, verified instruction by instruction against TI's disassembler and differentially against the same C compiled for the Mac. **Decision: every firmware change runs in the simulator against board models before it runs on the unit.** This paid for itself many times.
- **Captured the running stock firmware's peripheral registers** to the card. Copying the stock McASP and clock set-up gave us audio output from a standalone chord synth on the first try on hardware.
- **App launcher**: a modified stock firmware with a RUN APP menu item that loads `APP.BIN` from the card. **Decision: develop as loadable apps rather than reflashing.** A reflash takes minutes and goes through the updater; an app loads in seconds.
- **A Forth interpreter over USB serial** as the first app, which needed our own USB device driver (the controller never sees VBUS on the R8, so it runs in forced device mode). **Decision: Forth for exploration**, because peeking and poking registers interactively answers many questions faster than a build.

## 8 November: storage, audio, the panel and the clock

- **SD card driver and USB mass storage**: the Mac mounts the R8's card through the app, so new builds are copied over USB. The first read-write mount damaged the card's FAT, which led to finding the controller's 4 KB multi-block write fault. The FAT was repaired from its backup copy. **Lesson: test writes on unused blocks first.**
- **RELOAD**: restart a new `APP.BIN` without a power cycle. This made the edit-build-test loop a minute or two.
- **Audio in and out**, measured with a USB audio interface on the Mac acting as "ears". Found the transmitter stall after an underrun.
- **A variable-speed looper** (4 loops, faders for speed and level), with audio from a timer interrupt feeding the McASP FIFO.
- **The full panel map.** All keys and LEDs, using the manual, the stock firmware and the hardware. TEMPO was missing from the panel link; tracing the manual's description of the TEMPO pop-up back through the code found it on a DSP GPIO pin. LEDs were mapped by lighting each bit and photographing the panel with a phone camera.
- **Switched to TI's disassembler** (`dis6x`, part of the compiler) for reading stock code, by wrapping raw firmware slices in a minimal ELF file. Ghidra had no C6000 support.
- **Audio clock**: traced from the stock clock task to a board-ID check and an I2C clock chip, then switched the R8 between 44.1 and 48 kHz from Forth. The first C version hit an I2C timing race the Forth version had hidden.
- **DMA and SD**: multi-block reads hit the same 4 KB fault as writes, whether CPU or DMA fed the FIFO. **Decision: 7-block runs everywhere**; they still doubled read speed. (Superseded 14 Nov: the fault turned out to be a missing DMATRIG bit; see below.)
- **Recording**: input monitoring and recording into looper tracks, with panel controls chosen by the user (REC, then a track key).

## 9 November: saving to the card, and a channel bug

- **Saving recordings as WAV files** needed a FAT writer. **Decision: conservative by design** (free clusters only, data before metadata, never extend a directory), and checked with macOS's own disk checker on simulated card images before any write to the real card.
- **The first saved file had left and right swapped**, which exposed a bug present since the looper's first day: both McASP directions started one slot off, and the two errors cancelled in everything we had listened to. Fixed by reading the McASP's slot counter instead of counting words.
- **A host-coordination mistake**: making the card "disappear" from the Mac while the R8 wrote to it confused macOS badly enough that it stopped mounting the card. **Decision: refuse to write while the host has the card mounted**, which the host signals with standard SCSI commands.

## 11 November: several apps, and a way back to stock

- **An app chooser** as `APP.BIN`, starting any app in `A:\R8APPS`. It passes each app its own path, so `RELOAD` still works.
- **Restarting into the stock firmware** from an app. The PLL controller's software reset turned out to be locked; Timer64P1 in watchdog mode resets the chip exactly like power-on.
- **Utilities that run inside the stock firmware.** The user wanted tools that return to the normal R8 menu, which an app cannot do. **Decision: a second loader, RUN UTIL, that calls code from the card inside the running stock firmware**, using Zoom's file API, menus and dialogs. One more firmware update; after that, new utilities are file copies.

## 12 November: utilities on hardware, and the stock audio interrupt

- **RUN UTIL and a menu of utilities** worked on hardware, after a diagnostic utility dumping the stock folder-listing structure to settle its layout.
- **A file picker built from stock menus.** Hardware runs found that `*.*` skips folders, that menu rows are two columns with a fixed divider, and where long names live. A black bar on scrolling rows was traced to a bug in the stock scroller and worked around.
- **Direction change from the user: audio utilities rather than file management.** VARISPD (varispeed into a new file, speed set with the wheel through the stock value editor) came first. It also showed that the stock create call accepts long file names.
- **Playing and recording inside the stock firmware.** Tracing the stock audio interrupt found a per-block pointer a utility can wrap. A test utility showed the headphones and line out are separate outputs and measured the round trip (47.3 samples). REAMP followed; on hardware its recording lined up with the source within 0.8 sample.
- **The Blippoo app**, a chaotic synthesizer played from the faders, simulated with the board models before it reached the card.

## 13 November: recording from an app, and the pads

- **Blippoo REC** saved its takes as new WAV files with the looper's FAT code; the card checked clean.
- **The pads.** A logging app (PADTEST) recorded every panel byte while the user pressed pads. It corrected our pad numbering (one bit per pad), showed there is no pressure, and found that the key `A7`, thought unused, is the REPEAT/STOP pad.
- **A lesson about redundant tests:** a key logger written the day before had only re-confirmed event codes that the existing key map already predicted. **Decision: check the notes first, and run a test only to confirm a stated prediction.**
- **Power, phantom and the display.** A second logging app (POWERLOG) and firmware tracing found the USB-power pin, the battery thresholds, the PHANTOM switch (an input; the DSP switches phantom power itself) and the backlight and contrast controls, each checked on the unit (section 5).
- **REPEAT/STOP traced** in the stock pad code: rolls on the rhythm pads and looped playback on the loop-track pads (appendix A).
- **The Forth app split in two.** The looper had grown into the Forth app. **Decision: a core that apps build on through a few hooks**, with the plain Forth app for ad hoc experiments and the sampler as a separate app (section 8).

## 14 November: the 4 KB fault solved, and documentation for others

- **The SD fault was one missing bit.** Stock's driver sets MMCCMD's DMATRIG bit on every command. With it, one command reads or writes at least 128 blocks exactly, shown with two Forth words that only read, or only write to unused blocks (`SD-RCMP`, `SD-WCMP`). **Decision: the driver uses DMATRIG and runs of up to 128 blocks**, then a 22.5 MHz card clock, stock's setting for a card without high-speed mode. Through the driver on the R8: reads 10.3 MB/s (3.45 MB/s before), writes 5.3–5.7 MB/s, every transfer compared exact. Writes didn't get faster with the clock, which suggests the card's programming time limits them. A Blippoo save went from about 15 s to about 5 s.
- **An original LCD font.** The apps had copied their 5×7 font out of the stock firmware at build time, so app binaries carried Zoom's font data. A first redraw kept the usual 5×7 letter shapes and matched the stock bitmaps for 59 of 95 glyphs. A squared design leaves only 8 identical (space, `>`, H, I, L, T, `_`, x), and the apps no longer need `fw/` to build. It reads well on the R8's LCD.
- **Getting started, from a clean copy.** Building everything from a fresh copy of the repository produced apps byte-identical to the ones running on the unit, which is what the [walkthrough](getting-started.md) describes.
