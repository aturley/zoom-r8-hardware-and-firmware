# Appendix B: addresses and registers

A reference for firmware writers: the stock-firmware addresses we used, the C6748 registers we program, and the values we write.

**Version warning.** Every address in the stock firmware (code, data, tables, strings, call sites, file offsets) applies **only to stock firmware SYSTEM 1.17**. Other versions will put things elsewhere. Find them again before using any of them on another version. Addresses of the DSP's own peripherals (0x01xxxxxx) are the C6748's and do not depend on the firmware version. Memory regions that are "idle while stock runs" are a fact about 1.17 too.

**Confidence:** *hardware* = confirmed on our R8; *firmware* = read from stock 1.17 only; *inferred* = our best reading, untested. A function marked *hardware* was called or hooked by our code on the R8 and behaved as described.

**Where:** section numbers link to this writeup. Source files are relative to the repository root (for example `cbuild/sampler/looper.c`). `B14[n]` means the word at the stock data page pointer (0xC0812B20) + 4 × n.

## Stock firmware 1.17

### Boot, AIS image and flash layout

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| file 0x00 | Tag `ZOOM/D224` | kept as is | firmware | [4](04-stock-firmware.md) |
| file 0x20 | Version string `1.17` | kept; can be edited (same length) | hardware | [4](04-stock-firmware.md), [9](09-techniques.md) |
| file 0x34 | Checksum: 32-bit byte sum from 0x40 to the end | recomputed after every change (`r8fw.py fixsum`) | hardware | [4](04-stock-firmware.md), `tools/r8fw.py` |
| file 0x40 | AIS script (magic 0x41504954) | standalone builds reuse its PLL and DDR2 commands | hardware | [4](04-stock-firmware.md), `tools/standalone_c.py` |
| file 0x2126E8–0x220040 | 0xFF padding, about 55 KB, flashed but never loaded | holds our patch code; blob at file 0x212700 | hardware | [4](04-stock-firmware.md), `tools/menudump.py` |
| file 0x220040 | Rhythm samples (16-bit PCM, about 4.7 MB) | none | firmware | [4](04-stock-firmware.md) |
| flash 0x000000–0x07FFFF | BOOT region (R8BOOT) | never written | firmware | [2](02-safety-and-recovery.md) |
| flash 0x080000–0x79FFFF | SYSTEM region; flash = 0x80000 + file offset − 0x40 | written only by the stock updater | hardware (dump matched the file) | [2](02-safety-and-recovery.md) |
| flash 0x2A0000 | WAVE (rhythm) inside SYSTEM | none | firmware | [2](02-safety-and-recovery.md) |
| flash 0x2926C0 | Our patch blob (file 0x212700) | read by the cave loader | hardware | `tools/menudump.py` |
| flash 0x7A0000 | `ZOOM/R8` "image valid" marker | none | firmware | [2](02-safety-and-recovery.md), [4](04-stock-firmware.md) |
| flash 0x7B0000–0x7FFFFF | Version strings and settings | none | firmware | [2](02-safety-and-recovery.md) |
| 0xC07FAB00 | `_c_int00`, AIS jump target | none | firmware | [4](04-stock-firmware.md) |
| 0xC07AF808 | Updater (R8BOOT has the same code) | none | firmware | [4](04-stock-firmware.md) |
| 0xC07D1550 | Flash read: (flash address, buffer, length) | the cave loader copies our blob from the padding to RAM | hardware | [7](07-building-custom-firmware.md), `tools/menudump.py` |
| 0xC06D9800–0xC06D9DE0 | Code cave: SYS/BIOS name strings, printed only on errors | small hooks (about 1.3 KB used) | hardware | [4](04-stock-firmware.md), `tools/faderbar.py` |
| 0xC0715F40 | Start of `.text` | none | firmware | [5](05-hardware.md) |
| 0xC0604668 | Start of `.const` | none | firmware | [5](05-hardware.md) |
| 0xC0812B20 | Data page pointer B14 | base for every `B14[n]` below | firmware; used on hardware | [4](04-stock-firmware.md) |
| 0xC035D850 | SYS/BIOS task table: 22 entries of 68 bytes, entry function at +40 | none | firmware | [4](04-stock-firmware.md) |
| 0x11813C00 | Stock interrupt vector table (L2) | overwritten when an app takes over | firmware | [5](05-hardware.md) |

The task entry points and driver address ranges are listed in [section 4](04-stock-firmware.md) and are not repeated here.

### Menu engine and menu rows

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC078ED80 | `menu_engine(desc, 0, 0, 0)`: runs a menu until EXIT | opens our menus (R8 UTILS, file picker, utility pages) | hardware | `cbuild/r8.h`, `cbuild/utils/browse.c` |
| 0xC07AE620 | Where TOOL opens SD CARD with `menu_engine(desc, 0, 0, 0)` | the calling convention we copy | firmware | `cbuild/utils/utilmenu.c` |
| 0xC072FEB8 | The `menu_engine` call in the TRACK EDIT menu builder | patched to add REVERSE (returns to 0xC072FEC4) | hardware | `tools/menudump.py`, `tools/appmenu.py` |
| 0xC07317D8 | TRIM's item handler | REVERSE's hook recognises the TRACK EDIT menu by it | hardware | `tools/revmenu.py` |
| 0xC06DFFA8 | SD CARD menu descriptor (stock count 3) | count set to 4 (RUN APP) or 5 (RUN UTIL); list pointer at +32 replaced for 5 items | hardware | `tools/appmenu.py`, `tools/utilmenu.py` |
| 0xC0715700 | SD CARD item list, 4 slots; the next menu's list follows (slot 4 = 0xC06E22CC) | slot 3 = RUN APP; for RUN UTIL a new 6-word list in the cave | hardware | `tools/appmenu.py`, `tools/utilmenu.py` |
| 0xC06DFEB8, 0xC06DFF08, 0xC06DFF58 | Item records EXCHANGE, FORMAT, REMAIN (80 bytes each) | FORMAT's record is the template for ours | hardware | `tools/test_appmenu.py` |
| 0xC07ADF74 | SD CARD's event callback (descriptor +36) | used as the callback of every menu we build | hardware | `cbuild/utils/utilmenu.c` |
| 0xC06E995D | String stock records hold at +48..+60 (soft-key labels) | copied into our records | hardware | `cbuild/utils/browse.c` |
| 0xC078F3F8 | Where the engine stores the chosen index in state word 0, then calls the record's handler | one handler per menu level | hardware (behaviour) | `cbuild/utils/browse.h` |
| 0xC078EE40 | Engine reads its starting cursor from state word 0 | `show_items(..., start)` | firmware | `cbuild/utils/browse.c` |
| 0xC078EFE4 | Wheel edit (event 0x10000000): adds the movement to `*rec[7]` within `rec[9]`..`rec[10]`, then calls `rec[11]` and redraws | VARISPD's SPEED field | hardware | `cbuild/utils/browse.h` |
| 0xC078EB0C | Row draw routine; always draws the column divider at x = 60 | label/value layout | hardware | `cbuild/utils/browse.h` |
| 0xC078E684 | Row drawer: label at x = 0, value at x = 62 from `rec[8](s)` | value strings for our rows | hardware | `cbuild/utils/browse.c` |
| 0xC078E7C4, 0xC078E980 | Return addresses of the value call for the selected row and the other rows | `rowval.asm` passes the return address so we can tell them apart | hardware | `cbuild/utils/rowval.asm` |

**Descriptor layout** (12 words, as we build it; hardware): +0 item count, +4 title, +20 and +24 pointers to two state words (word 0 = selected index), +28 0x10013, +32 item list (null-terminated), +36 event callback.

**Item record layout** (80 bytes = 20 words; hardware unless marked): word 0 label; word 1 type (0 = no triangle; firmware); word 4 handler; word 6 flags (bit 0 set in stock records; bit 3 = the wheel does not edit this row; bits 4 and 5 wrap / always write, firmware); word 7 value pointer or `int *` for the wheel; word 8 value function `const char *f(const u32 *s)`, with `s[3]` = word 7; words 9 and 10 wheel min and max; word 11 called after a wheel edit; words 12–15 = 0xC06E995D. Non-selected values are cut to 11 characters; the selected value scrolls when longer than 10.

### UI events and dialogs

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC07B5B58 | `ui_event(timeout)`: waits on the UI mailbox `B14[25]` up to `timeout` ticks (negative = forever); 0 on timeout | utilities poll for STOP/EXIT with a 1-tick wait | hardware | `cbuild/r8.h`, `cbuild/utils/reamp.c` |
| event 0x10000 \| ID | Key press (ID = panel status − 0x80); 0x20000 \| ID release, 0x40000 \| ID hold | key handling inside stock | hardware (press, release); firmware (hold) | [Appendix A](appendix-a-panel-protocol.md) |
| 0x10013, 0x10018, 0x10020 | EXIT, ENTER, STOP press | `R8_EV_EXIT`, `R8_EV_ENTER`, `R8_EV_STOP` | hardware | `cbuild/r8.h`, `cbuild/utils/keylog.c` |
| 0x10000000 | Wheel event | the engine's wheel edit | hardware | `cbuild/r8.h` |
| 0x10028 | TEMPO (from GPIO GP6[0], not the panel) | none | firmware; pin hardware | [9](09-techniques.md) |
| 0xC0813E98 | Key-dispatch jump table, indexed by key ID 0..0x28 | none | firmware | [4](04-stock-firmware.md) |
| 0xC0712614 | TEMPO pop-up descriptor (entry 0x28 of the table above) | none | firmware | [9](09-techniques.md) |
| 0xC06FE848 | Control-surface table: key → Mackie Control function (DAW mode) | source of the key map | firmware | [4](04-stock-firmware.md) |
| 0xC0701280 | Key name strings for that table | source of the key map | firmware | — |
| 0xC07BAD0C | Key dispatcher; routes footswitch events (0x100000 \| value) to the CTRL IN action | none | firmware | — |
| 0xC05B60D0, 0xC0813E84 | CTRL IN setting; its jump table | none | firmware | — |
| 0xC07F670C | Prepare a message dialog (`pre_msg`) | before `show_msg` | hardware | `cbuild/r8.h` |
| 0xC0798878 | `show_msg(s)`: "Please Wait..." style dialog | REVERSE, REAMP, TONETEST | hardware | `cbuild/r8.h` |
| 0xC07988D0 | `dialog_close(ok)`: 1 after success (stock shows "Complete"), 0 before an error popup | as Trim does | hardware | `cbuild/r8.h` |
| 0xC0797D90 | `popup(s)` | errors and results | hardware | `cbuild/r8.h` |
| 0xC06E77DD | String "Please Wait..." | `R8_STR_PLEASE_WAIT` | hardware | `cbuild/r8.h` |
| 0xC06E77C5 | String for "no file" | `R8_STR_NO_FILE` | hardware | `cbuild/r8.h` |
| 0xC06E77CE | String for "protected" | `R8_STR_PROTECTED` | firmware | `cbuild/r8.h` |
| 0xC06E7960 | "ACCESS ERROR" | `R8_STR_ACCESS_ERROR` | hardware | `cbuild/r8.h` |
| 0xC06E785D | "Invalid File" | `R8_STR_INVALID_FILE` | hardware | `cbuild/r8.h` |

### File system and track calls (used by utilities and REVERSE)

All return 0 on success. Paths look like `A:\R8UTILS\HELLO.BIN`.

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC0763C6C | `open(&fd, path, mode, attr)`: mode 0 read, 2 read/write, 0x101 create (attr 0x80) | every file access; create makes long names | hardware | `cbuild/r8.h`, `cbuild/r8_stock.cmd` |
| 0xC0763B2C | `read(fd, buf, len, &got)` | loading apps, utilities, WAVs (32 KB chunks) | hardware | same |
| 0xC0762DB8 | `write(fd, buf, len, &n)` | saving WAVs and logs | hardware | same |
| 0xC0764050 | `seek(fd, off, whence)`: 2 from start, 0 relative | WAV header rewrite | hardware | same |
| 0xC0765C6C | `close(fd)` | | hardware | same |
| 0xC0762EE0 | `delete(path)`; refuses a file the recorder has attached | REVERSE, failed saves | hardware | same, [7](07-building-custom-firmware.md) |
| 0xC07639E8 | `rename(from, to)` | REVERSE | firmware | `tools/revmenu.py` |
| 0xC07DAECC | `info(path, info)`: 0 = exists; `info[0x133]` bit 0 = read-only | REVERSE | firmware | `cbuild/r8.h` |
| 0xC0764EC0 | `find_first(pattern, 0x17, find)` | folder listings | hardware | `cbuild/utils/browse.c` |
| 0xC0764B2C | `find_next(find)`; −10406 = no more | | hardware | same |
| 0xC0765188 | `find_close(find)` | | hardware | same |
| 0xC0771288 | Stock caller listing `\*.WAV` | the pattern for using the find calls | firmware | `cbuild/r8.h` |
| 0xC07FB800 | `strlen` | | hardware | `cbuild/r8.h` |
| 0xC0771DB8 | `card_check(what)`; TRIM uses 11 | before RUN APP, REVERSE, REAMP | hardware | `cbuild/r8.h` |
| 0xC077BDB4 | `track_file(track)`: full path of the track's WAV | REVERSE | hardware | `cbuild/r8.h` |
| 0xC077BCEC | `track_detach(track)` | REVERSE, before replacing a track's file | hardware | same |
| 0xC077BC14 | `track_attach(path, track)` | REVERSE | hardware | same |
| 0xC079240C | `clear_undo()` | REVERSE (Trim and Stretch call it too) | hardware | same |
| 0xC0813024 | Current track index (`B14` + 0x504) | REVERSE | hardware | `cbuild/r8.h`, `tools/menudump.py` |
| 0xC05B3240 | Stereo-link mask: bit t set = track t is linked (Trim uses `track & ~1`) | REVERSE | firmware | `tools/revmenu.py` |
| 0xC07FC460 | SYS/BIOS `Cache_wbInv(addr, len, type, wait)`; type 0x7FFF = all | before calling loaded code | hardware | `cbuild/r8.h` |
| 0xC07F8860 | SYS/BIOS `Cache_invL1pAll()` | after `Cache_wbInv` | hardware | same |
| 0xC07FC480, 0xC07FC440 | SYS/BIOS cache write-back; invalidate | none | firmware | — |

**Find structure** (`find_first`/`find_next`; hardware, UTILDIAG/LISTDIAG): +0x12 8.3 name (NUL-terminated); +0x1F long name (first byte 0 if none; later bytes stale); +0x11F attributes (0x01 read-only, 0x02 hidden, 0x04 system, 0x10 folder, 0x20 archive); +0x120 size (u32); +0x124 created and +0x138 modified (day, month, year u16, 2 bytes, hour, minute, second). We reserve 0x200 bytes. `*.*` matches DOS-style (needs a dot), so use `dir\*` to see folders.

### Audio interrupt, buffers and hook pointers

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0x1180E738 | Stock audio interrupt, every 8 frames | runs our block function through the hook below | firmware; hooks hardware | `cbuild/utils/tonetest.c` |
| 0xC08137F4 | `B14[821]`: buffer-half flag, 1 while transmit DMA is in the first half (so this block uses the second) | picks the half to read and write | hardware | `cbuild/utils/reamp.c` |
| 0x11F02E68, 0x11F02E6C | Per-block function pointers called before the frame loop | none | firmware | `cbuild/utils/tonetest.c` |
| 0x11F02E70 | Per-block pointer called after the frame loop | none | firmware | same |
| 0x11F02E74 | Last per-block pointer; defaults to a no-op at 0x1180E5A0 | wrapped by TONETEST and REAMP (call the original, then overwrite the block) | hardware | `cbuild/utils/reamp.c` |
| 0x11F02E78… | Chain of 20 per-frame pointers | none | firmware | `cbuild/utils/tonetest.c` |
| 0x11F02828, 0x11F02868 | Receive buffer halves: 8 frames × (left, right), 24-bit left-justified in 32 | input = `0x11F02828 + 8 × (8h + f)` | hardware | same, [5](05-hardware.md) |
| 0x11F028A8, 0x11F02928 | Transmit buffer halves: 8 frames × 4 words: serializer 2 L, serializer 4 L, serializer 2 R, serializer 4 R | words 0, 2 = headphones; 1, 3 = line out | hardware | same |
| 0xC0700A88 | Fader law: 128 × u16, 8192 = unity (100 = 0 dB, 127 = +12 dB) | our `fadergain.h` is built from its breakpoints | firmware | [5](05-hardware.md), `cbuild/forth/fadergain.h` |
| 0xC0603210 | LED bitmap the LCD task sends with panel command `89` | none | firmware | [Appendix A](appendix-a-panel-protocol.md) |
| near 0xC0748B80 | Beat timer that toggles the TEMPO LED (GP7[9]) | none | firmware | — |

Round trip line out → inputs, measured from the block written to the block read in the same hook call: 47 frames (hardware).

### Sample rate and audio clock

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC07D3FE0 | Returns the current sample rate (`B14[818]`) | REAMP checks the source WAV against it | hardware | `cbuild/r8.h` |
| 0xC07F3324 | `AudioClkMng` task; drains a request queue at 0xC0602C80 | none | firmware | [9](09-techniques.md), `cbuild/forth/clock.c` |
| 0xC07F3264 | Enqueue a clock request (a, b) | none | firmware | — |
| 0xC07961AC, 0xC07E1C40, 0xC07EE568 | Its callers: project sample-rate code, init, `AIF_ManageTask` | none | firmware | — |
| 0xC07EAE94 | Stop the clock | we replay its I2C write | hardware (replayed) | `cbuild/forth/clock.c` |
| 0xC07EAD4C | Start the clock for a rate | we replay its I2C write | hardware (replayed) | same |
| 0xC07A73D8, 0xC07A73A4 | McASP stop; McASP and EDMA re-init (rate-independent) | not needed: the switch works live | firmware | — |
| 0xC07EACC0 | Write to the clock chip (reg, buf, n) | none | firmware | — |
| 0xC07E3564, 0xC07E3320 | I2C0 driver | we copy its "wait for ICMDR.STP" rule | firmware | [6](06-quirks.md) |
| 0xC079F328 | Reads the board ID straps; stored at `B14` + 0x92C | `clk_board()` does the same | firmware; ID 7 hardware | `cbuild/forth/clock.c` |

### Menu value scroller

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC0813790 | `B14[796]`: "scroller running" flag | cleared after stopping the scroller | hardware | `cbuild/utils/browse.c` |
| 0xC07EC120 | Start the scroller for a row | none (the row drawer calls it) | hardware | same |
| 0xC07EC184 | Stop the scroller | called when the selection moves to another long value (works around a stock bug that leaves a black bar) | hardware | same |

### Power off

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC07EB58C | Shutdown ("Goodbye See you!", then "Please Power Off" while the switch reads on) | the model for our power-off | firmware | `cbuild/synth/main.c` |
| 0xC079F2A0 | Reads the power switch (GP8[12]) | none | firmware | — |
| 0xC079F2B0 | Clears GP7[0] (power off) | none | firmware | — |

### Panel link (UART2)

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC07BBCA0 | Opens UART2: `uart_open(2, 166666, …)` | we copy its register values (below) | firmware | [5](05-hardware.md), `cbuild/synth/panel.c` |
| 0xC07BBD38 | `AudioCmdRcv` task (panel link receive) | none | firmware | [4](04-stock-firmware.md) |
| 0xC07D54A8 | UART read | hooked by the panel-link logger and the fader bar | hardware | `tools/faderbar.py` |
| 0xC07BBCF8 | The UART read call in the receive task | retargeted to our hook | hardware | `tools/faderbar.py` |
| 0xC07D4C04–0xC07D5600 | UART driver | call sites retargeted for the logger | hardware | [4](04-stock-firmware.md), [9](09-techniques.md) |
| 0xC07BB740, 0xC07BB800, 0xC07BB850 | Panel packet send (not documented here) | none | firmware | — |

### LCD

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC05F4178 | 1 KB frame buffer: byte = page × 128 + column, bit 0 = top row | patched builds drew into it | hardware | [4](04-stock-firmware.md) |
| 0xC0748250 | `lcd_flush_pages`: hands dirty pages to `lcd_put` | none | hardware | [4](04-stock-firmware.md) |
| 0xC07CCF34 | `lcd_put`: wakes the LCD hardware task | the fader bar sent pages through it | hardware | `tools/faderbar.py` |
| 0xC081311A | Freeze counter: nonzero stops flushing | took over the screen and gave it back | hardware | [4](04-stock-firmware.md) |
| 0xC07CCD78 | `MainMenu` task (really the LCD hardware task) | none | firmware | [4](04-stock-firmware.md) |
| 0xC07D92D0 | SPI driver | none | firmware | [4](04-stock-firmware.md) |
| 0xC07CC994 | LCD init sequence (reset pulse, then the ST7565 commands) | `cbuild/synth/lcd.c` sends the same bytes | hardware | [5](05-hardware.md) |
| 0xC07CCB94 | `lcd_contrast(v)`: sends `81 v` (v masked to 6 bits) under the LCD lock | our `lcd_contrast()` sends the same command (Forth `LCD-VOL`) | firmware | [5](05-hardware.md), `cbuild/synth/lcd.c` |
| 0xC07BDF94 | Applies a CONTRAST setting 0–7 through the table at 0xC0713BE0 (13, 14, 15, 16, 18, 20, 22, 25) | `LCD_CONTRAST_STOCK` copies the table (Forth `CONTRAST`) | firmware | [5](05-hardware.md), `cbuild/synth/lcd.c` |
| 0xC05B6094, 0xC05B6098 | CONTRAST (0–7) and LIGHT (0 off, 1 on, 2 15sec, 3 30sec) settings | none | firmware | [5](05-hardware.md) |
| 0xC07B5AC8 | `backlight_apply`: sets both backlight pins from LIGHT and loads the countdown `B14[430]` (15000/30000); called after every non-system UI event | none | firmware | [5](05-hardware.md) |
| 0xC07BDFB4 | `backlight(on)`: GP7[14] and GP7[15] together | none | firmware | [5](05-hardware.md) |
| 0xC079F014, 0xC079F040 | Set or clear GP7[14], GP7[15] | none | firmware | [5](05-hardware.md) |

### Phantom power

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC05B609C | PHANTOM voltage setting: 0 = 48 V (default), 1 = 24 V | none | firmware | [5](05-hardware.md) |
| 0xC06E25CC, 0xC0715AF8 | PHANTOM menu record (TOOL > SYSTEM) and its value list ("48V", "24V") | none | firmware | [5](05-hardware.md) |
| 0xC07ED8EE | PHANTOM menu change handler: GP7[10] off, set GP7[8], wait 100 ticks, GP7[10] from the switch | none | firmware | [5](05-hardware.md) |
| 0xC0771AAC | Sets the voltage pin: GP7[8] = 1 − setting | none | firmware | [5](05-hardware.md) |
| 0xC0771A60 | Phantom on/off with settling (wait 5, GP7[10], wait 5); state cached at B14[773] | none | firmware | [5](05-hardware.md) |
| 0xC079F5D8 | PHANTOM switch (GP8[11]) change handler: drives GP7[10], posts `0x10104` / `0x20104` | none | firmware | [5](05-hardware.md) |
| 0xC079F200, 0xC079F22C, 0xC079F2F0 | GPIO wrappers: GP7[10] = arg, GP7[8] = arg, read GP8[11] | none | firmware | [5](05-hardware.md) |

### Drivers we followed

| Address | What it is | How we use it | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0xC076BA30 | USB0 start routine | our bring-up repeats its steps | hardware (replayed) | `cbuild/forth/usb.c` |
| 0xC07A60E0 | McASP0 driver | none | firmware | [4](04-stock-firmware.md) |
| 0xC07A7550–0xC07A8844 | SD/MMC controller driver; function table at 0xC071344C (slot 5 = command `0xC07A7A60`, slot 6 = command data phase `0xC07A7E88`, slot 9 = clock `0xC07A78C8`) | none | firmware | [6](06-quirks.md) |
| 0xC05FD210, 0xC05FD240 | Controller descriptors: MMCSD0 (0x01C40000) and MMCSD1 (0x01E1B000, interrupt event 0x44, ISR 0xC07A7744) | none | firmware | [6](06-quirks.md) |
| 0xC0706F60 | MMCCMD word per command index (for example 22 = CMD17 `0x2211`, 23 = CMD18 `0x2212`, 32 = CMD24, 33 = CMD25, 5 = CMD6 with data); DMATRIG (bit 16) is ORed in when issued | compare with ours | firmware | [6](06-quirks.md) |
| 0xC07A0428, 0xC07A0268 | Block read / write: one CMD17 or CMD18 (CMD24 or CMD25) for the whole count, then CMD12 | none | firmware | [6](06-quirks.md) |
| 0xC07A04F8, 0xC07A0F24 | Chunked read wrapper and its setter (chunk sizes at 0xC06000A0 / 0xC06000AC); the setter has no callers | none | firmware | [6](06-quirks.md) |
| 0xC07A834C | Builds the data-phase DMA descriptors (MMCDRR or MMCDXR, 4 bytes × 16 per event) and starts them through the table at B14[889] | none | firmware | [6](06-quirks.md) |
| 0xC079FB8E | Card set-up: CMD6 high-speed switch (0xC079FACC), then the clock from the speed code (0x5A after a switch) | none | firmware | [6](06-quirks.md) |
| 0xC07F2220 | GPIO direction (bank, pin, dir; 0 = output) | none | firmware | — |
| 0xC07F22C8, 0xC07F2324, 0xC07F2380 | GPIO set, clear, read (bank, pin) | none | firmware | — |

## C6748 peripherals (version-independent)

Register names follow TI's documentation. Offsets are from the base in the group's first row.

### SYSCFG0, pin multiplexing and USB PHY

| Register | Address | What we write | Confidence | Where |
| --- | --- | --- | --- | --- |
| SYSCFG0 base | 0x01C14000 | | | `cbuild/synth/hw.h` |
| KICK0R, KICK1R | +0x38, +0x3C | 0x83E70B13, 0x95A4F1E0 (unlock) | hardware | `cbuild/synth/hw.c` |
| PINMUX0–19 | +0x120 + 4n | stock values (below), except PINMUX4 | hardware | same |
| PINMUX4 | +0x130 | never written: panel UART pins, depends on board revision; leave as R8BOOT set it | hardware | [6](06-quirks.md) |
| CFGCHIP2 | +0x184 (0x01C14184) | 0x0932 as stock, then OTG mode 2 (force device): 0x4932 | hardware | `cbuild/forth/usb.c` |

Stock PINMUX values (index 0–19; 4 is skipped): `0x88111111 0x88888888 0x01141248 0x44441111 — 0x88188188 0x88888888 0x88888888 0x88888888 0x88888888 0x88888888 0x88888888 0x88888888 0x88888888 0x00000088 0x88888800 0x88888888 0x00088888 0x88822280 0x18822288` (hardware; `pinmux_stock()` in `cbuild/synth/hw.c`).

### PSC (power and sleep controller)

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| PSC0 base | 0x01C10000 | defined, not used by our apps | | `cbuild/synth/hw.h` |
| PSC1 base | 0x01E27000 | | | same |
| PTCMD | PSC1 + 0x120 | 1 = GO for power domain 0 | hardware | `cbuild/synth/hw.c` |
| PTSTAT | PSC1 + 0x128 | wait for bit 0 to clear | hardware | same |
| MDSTAT n | PSC1 + 0x800 + 4n | state in bits 0–5; 3 = enabled | hardware | same |
| MDCTL n | PSC1 + 0xA00 + 4n | NEXT in bits 0–4: 0 sync reset, 2 disable, 3 enable | hardware | same, `cbuild/forth/usb.c` |

PSC1 modules we enable: USB0 = 1, GPIO = 3, McASP0 = 7, SPI1 = 10, UART2 = 13, MMCSD1 = 18 (all hardware).

### GPIO

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| GPIO base | 0x01E26000 | | | `cbuild/synth/hw.h` |
| Bank pair p (0–4) | 0x01E26010 + 0x28 × p | +0x00 DIR (1 = input), +0x04 OUT_DATA, +0x08 SET_DATA, +0x0C CLR_DATA, +0x10 IN_DATA | hardware | same |
| IN_DATA01 | 0x01E26020 | board ID straps GP0[7], GP1[5], GP1[4] (bits 7, 21, 20) | hardware (reads 7) | `cbuild/forth/clock.c` |
| SET_DATA67, CLR_DATA67, IN_DATA67 | 0x01E26090, 0x01E26094, 0x01E26098 | LEDs and power hold (GP7 = bits 16–31); TEMPO key GP6[0] = bit 0 | hardware | [Appendix A](appendix-a-panel-protocol.md) |
| IN_DATA8 | 0x01E260C0 | POWER switch GP8[12] = bit 12 | hardware | `cbuild/forth/core.c` |
| CLR_DATA67 bit 16 | GP7[0] | power off | hardware | same |

Pin assignments are in [section 5](05-hardware.md). Stock GPIO state, captured from the running stock firmware and written by our standalone builds before audio start (`STOCK_GPIO` in `cbuild/synth/stockstate.h`; OUT first, then DIR; hardware):

| Pair | DIR offset | DIR | OUT_DATA |
| --- | --- | --- | --- |
| 0/1 | 0x10 | 0x07BCFC80 | 0x00400000 |
| 2/3 | 0x38 | 0x00002400 | 0x00004000 |
| 4/5 | 0x60 | 0x00000000 | 0x00000000 |
| 6/7 | 0x88 | 0x001E4C3F | 0xE1C10000 |
| 8 | 0xB0 | 0xFFFFFCE1 | 0x00000300 |

The polled pass-through (`cbuild/forth/aio.c`) writes the same values but keeps the LCD pins (GP2[14], GP8[8], GP8[9]) as they are.

### Panel UART (UART2)

| Register | Address | Value | Confidence | Where |
| --- | --- | --- | --- | --- |
| UART2 base (RBR/THR) | 0x01D0D000 | | | `cbuild/synth/panel.c` |
| PWREMU_MGMT | +0x30 | 0 first; 0x6001 last (out of reset) | hardware | same |
| DLL, DLH | +0x20, +0x24 | 67, 0: 180 MHz / 16 / 67 ≈ 167.9 kbaud | hardware | same |
| MDR | +0x34 | 0 (16× oversampling) | hardware | same |
| FCR | +0x08 | 0, 1, then 0x0F (FIFOs on) | hardware | same |
| IER | +0x04 | 0 (polled) | hardware | same |
| MCR | +0x10 | 0 | hardware | same |
| LCR | +0x0C | 3 (8N1) | hardware | same |
| LSR | +0x14 | bit 0 data ready, bit 5 transmit empty | hardware | same |

These copy the stock open call (0xC07BBCA0).

### LCD (SPI1)

| Register | Address | Value | Confidence | Where |
| --- | --- | --- | --- | --- |
| SPI1 base | 0x01F0E000 | | | `cbuild/synth/lcd.c` |
| SPIGCR0 | +0x00 | 0, then 1 | hardware | same |
| SPIGCR1 | +0x04 | 3 (master), then 0x01000003 (enabled) | hardware | same |
| SPIINT0, SPILVL | +0x08, +0x0C | 0 | hardware | same |
| SPIPC0 | +0x14 | 0x0E01 | hardware | same |
| SPIDAT1 | +0x3C | 0, then one byte per transfer | hardware | same |
| SPIBUF | +0x40 | wait for bit 31 (RXEMPTY) to clear after each byte | hardware | same |
| SPIDELAY | +0x48 | 0x03030000 | hardware | same |
| SPIDEF | +0x4C | 0 | hardware | same |
| SPIFMT0 | +0x50 | 0x00021108: prescale 17 (10 MHz), polarity 1, phase 0, 8 bits | hardware | same |

Chip select is GPIO GP2[14], A0 GP8[9], reset GP8[8]. Init bytes: `AE A2 A0 C0 26 81 0D A6 A4 40 B0 10 00 2C`, then `2D 2F 2C` repeated with 10 ms gaps (ends `2D 2F`), then `81 0D AF` (hardware; `lcd_init()`).

### Audio (McASP0)

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| McASP0 base | 0x01D00000 | | | `cbuild/synth/hw.h` |
| PFUNC, PDIR | +0x10, +0x14 | stock values | hardware | `cbuild/synth/stockstate.h` |
| GBLCTL | +0x44 | read back to see reset bits latch; 0 stops everything (the launcher does this on take-over) | hardware | `cbuild/launcher/launch.c` |
| AMUTE, DLBCTL, DITCTL | +0x48, +0x4C, +0x50 | 0 | hardware | `cbuild/sampler/looper.c` |
| RGBLCTL | +0x60 | receive bits 0–4: RCLKRST, RHCLKRST, RSRCLR, RSMRST, RFRST | hardware | same |
| RMASK, RFMT, AFSRCTL, ACLKRCTL, AHCLKRCTL, RTDM, RINTCTL | +0x64 … +0x7C | stock values (below); RINTCTL 0 | hardware | same |
| RSTAT | +0x80 | bit 0 overrun; bit 3 RTDMSLT (even slot on the wire) | hardware | same, [6](06-quirks.md) |
| RCLKCHK, REVTCTL | +0x88, +0x8C | stock; REVTCTL 0 with the FIFO | hardware | same |
| XGBLCTL | +0xA0 | transmit bits 8–12: XCLKRST, XHCLKRST, XSRCLR, XSMRST, XFRST | hardware | same |
| XMASK, XFMT, AFSXCTL, ACLKXCTL, AHCLKXCTL, XTDM, XINTCTL | +0xA4 … +0xBC | stock values; XINTCTL 0 | hardware | same |
| XSTAT | +0xC0 | bit 3 XTDMSLT; bit 5 XDATA; write 0xFFFF to clear | hardware | same |
| XCLKCHK, XEVTCTL | +0xC8, +0xCC | stock; XEVTCTL 0 with the FIFO, 1 when the CPU writes XBUF | hardware | same, `cbuild/synth/audio.c` |
| SRCTL n | +0x180 + 4n | serializer 1 receives, 2 and 4 transmit | hardware | same |
| XBUF n / RBUF n | +0x200 + 4n | CPU access (configuration bus) when XBUSEL/RBUSEL = 1 | hardware | `cbuild/forth/aio.c` |
| WFIFOCTL, WFIFOSTS | 0x01D01010, 0x01D01014 | write FIFO control; level in bits 0–7 | hardware | `cbuild/sampler/looper.c` |
| RFIFOCTL, RFIFOSTS | 0x01D01018, 0x01D0101C | read FIFO control; level in bits 0–7 | hardware | same |
| Data port | 0x01D02000 | all FIFO reads and writes | hardware | same |

### Timers and interrupts

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| Timer64P1 base | 0x01C21000 | our audio tick and the restart watchdog | | `cbuild/sampler/looper.c`, `cbuild/synth/hw.c` |
| TIM12, TIM34 | +0x10, +0x14 | 0 | hardware | same |
| PRD12, PRD34 | +0x18, +0x1C | audio: 24 MHz / 8000 − 1 = 2999; watchdog: 0x6000, 0 | hardware | same |
| TCR | +0x20 | 0 stop; 0x80 continuous (ENAMODE12) | hardware | same |
| TGCR | +0x24 | audio: set bit 0 (stock value 5); watchdog: 0, then 0xB | hardware | same |
| WDTCR | +0x28 | 0xA5C64000, then 0xDA7E4000: watchdog runs, resets the chip in about 1 ms | hardware | [7](07-building-custom-firmware.md), `cbuild/synth/hw.c` |
| INTCTLSTAT | +0x44 | 3: enable and clear the period interrupt (the event fires even with the enable bit clear) | hardware | `cbuild/sampler/looper.c` |
| Event 40 | | Timer64P1 TINT12; stock runs it at 1 kHz | hardware | — |
| INTC base | 0x01800000 | | | `cbuild/sampler/looper.c` |
| INTMUX3 | 0x0180010C | bits 24–31 = event for INT15; we write 40 (stock already routes it) | hardware | same |
| CPU ISTP, IER, ICR | control registers | ISTP = our vector table; IER bits 15 and 1 (NMIE); launcher clears IER and ICR = 0xFFF0 | hardware | same, `cbuild/launcher/launch.c` |
| EDMA CC0 completion (event 8) | | routed to DSP INT7 by stock | firmware | [5](05-hardware.md) |

### Restart (PLL0)

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| RSTYPE | 0x01C110E4 | reads 1 after power-on | hardware | — |
| RSCTRL | 0x01C110E8 | reads 0x00010003 (locked); the 0x5A69 key does not unlock it; no reset. Use the watchdog. | hardware | [7](07-building-custom-firmware.md) |

### I2C0 (clock chip on board 7)

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| I2C0 base | 0x01C22000 | left by the boot loader as master at about 100 kHz (ICPSC 2, ICCLKL/H 35) | hardware | `cbuild/forth/clock.c` |
| ICSTR | +0x08 | NACK 0x02, XRDY 0x10, SCD 0x20, BB 0x1000; write 1 to clear | hardware | same |
| ICCNT | +0x14 | bytes including the register byte | hardware | same |
| ICSAR | +0x1C | 0x54 | hardware | same |
| ICDXR | +0x20 | register byte, then data | hardware | same |
| ICMDR | +0x24 | 0x6E20 start a write; 0x4220 idle; wait for STP (0x0800) to clear after each transfer; 0x6220 = lost START | hardware | same, [6](06-quirks.md) |

Clock chip writes (board 7, hardware): register 0xF7 ← `10`, then 0xF5 ← `01 0E 00 00 05 3B 72 N F0 F1 F2`. 44.1 kHz: N = 0x41, F = 0x01CC5. 48 kHz: N = 0x47, F = 0x31D93. Other boards (firmware): GP0[9] and GP1[15], see [section 5](05-hardware.md).

### SD card (MMCSD1)

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| MMCSD1 base | 0x01E1B000 | | | `cbuild/forth/sd.c` |
| MMCCTL | +0x00 | 3 (DATRST, CMDRST), then 0 | hardware | same |
| MMCCLK | +0x04 | CLKRT, then CLKRT \| 0x100 (CLKEN); card clock = 180 MHz / (2 × (CLKRT + 1)): 224 → 400 kHz, 4 → 18 MHz, 3 → 22.5 MHz (ours since 2026-11-14) | hardware | same |
| MMCST0 | +0x08 | DATDNE 0, BSYDNE 1, RSPDNE 2, TOUTRD 3, TOUTRS 4, CRCWR 5, CRCRD 6, CRCRS 7, DRRDY 10; clears on read | hardware | same |
| MMCST1 | +0x0C | FIFOEMP bit 5, FIFOFUL bit 6 | hardware | same |
| MMCIM | +0x10 | 0 (polled) | hardware | same |
| MMCTOR, MMCTOD | +0x14, +0x18 | 0x1FFF, 0xFFFF | hardware | same |
| MMCBLEN, MMCNBLK | +0x1C, +0x20 | block length, block count (up to 128 per transfer since 2026-11-14; at most 7 before) | hardware | same |
| MMCDRR, MMCDXR | +0x28, +0x2C | data FIFO | hardware | same |
| MMCCMD | +0x30 | PPLEN bit 7, BSYEXP 8, response type bits 9–10, DTRW 11, **WDATX 13**, INITCK 14, DCLR 15, **DMATRIG 16** (set on every multi-block data command, or transfers of 4 KB and more fail) | hardware | same, [6](06-quirks.md) |
| MMCARGHL | +0x34 | argument | hardware | same |
| MMCRSP01–67 | +0x38 … +0x44 | 48-bit responses in MMCRSP67 | hardware | same |
| MMCFIFOCTL | +0x74 | bit 2 = 64-byte threshold (not 32) | hardware | same |

### USB0

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| USB0 base | 0x01E00000 | | | `cbuild/forth/usbregs.h` |
| CTRLR | +0x04 | bit 0 soft reset; bit 3 cleared (wrapper interrupt mode, as stock) | hardware | `cbuild/forth/usb.c` |
| INTSRCR, INTCLRR | +0x20, +0x28 | raw event source; write 1 to clear. Bits 0–4 TX endpoints, 8–12 RX, 18 bus reset | hardware | same |
| INTMSKCLR | +0x34 | 0x01FF1F1F (polled, no CPU interrupt) | hardware | same |
| MUSB core | +0x400 | FADDR +0x00, POWER +0x01 (0x40 = SOFTCONN), INTRTXE +0x06 (0x1F), INTRRXE +0x08 (0x1E), INTRUSBE +0x0B (0xFF), INDEX +0x0E, TXMAXP +0x10, CSR0/TXCSR +0x12, RXMAXP +0x14, RXCSR +0x16, COUNT0 +0x18, FIFO n +0x20 + 4n, DEVCTL +0x60, TXFIFOSZ/RXFIFOSZ +0x62/+0x63, TXFIFOADDR/RXFIFOADDR +0x64/+0x66 | hardware | `cbuild/forth/usbregs.h` |
| DEVCTL | core +0x60 | reads 0x80 with the cable in (no VBUS); set bit 0 (SESSION) after forcing device mode; then reads 0x99 | hardware | [6](06-quirks.md) |

Write INDEX before the endpoint CSR it selects, with a `NOP` between them (compiler hazard, [6](06-quirks.md)). FIFO RAM in 8-byte units: EP1 TX 64 bytes at 64, EP2 RX 64 at 128, EP3 TX 16 at 192, EP4 TX 64 at 256 and RX 64 at 320 (our layout).

### EDMA3

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| CC0 base | 0x01C00000 | stock: audio, I2C, SPI | firmware | [5](05-hardware.md) |
| CC1 base | 0x01E30000 | stock: SPI; our experimental SD reads | hardware | `cbuild/forth/sd.c` |
| EMR, EMCR | +0x300, +0x308 | missed events; launcher clears all | hardware | `cbuild/launcher/launch.c` |
| ECR, EECR, EESR | +0x1008, +0x1028, +0x1030 | event clear, event disable, event enable | hardware | same, `cbuild/forth/sd.c` |
| SECR | +0x1040 | secondary event clear | hardware | same |
| IECR | +0x1058 | interrupt disable | hardware | `cbuild/launcher/launch.c` |
| IPR, ICR | +0x1068, +0x1070 | interrupt pending, clear | hardware | same |
| QEECR | +0x1088 | QDMA event disable (0xFF) | hardware | same |
| PaRAM set n | +0x4000 + 32n | CC1 has no channel map: channel n uses set n | hardware | `cbuild/forth/sd.c` |
| CC0 PaRAM set 1 SRC | 0x01C04024 | the stock audio ISR reads it to find the free half | firmware | — |

Events: CC0 0 = McASP0 receive, 1 = McASP0 transmit (stock, firmware); CC1 28 = MMCSD1 receive (hardware); CC1 29 = MMCSD1 transmit (inferred from TI's table, untested). Stock audio PaRAM (firmware): event 1 set 1 linked 64 ↔ 65, ACNT 8, BCNT 16, no interrupt; event 0 set 0 linked 66 ↔ 67, ACNT 4, BCNT 16, TCC 0 interrupt; EER = 3, queue 0.

### Cache control

| Register | Address | Use | Confidence | Where |
| --- | --- | --- | --- | --- |
| L2CFG | 0x01840000 | launcher sets L2MODE = 0 (L2 all SRAM) | hardware | `cbuild/launcher/jump.asm` |
| L2WBINV | 0x01845004 | launcher: write back and invalidate L2 | hardware | same |
| L1PINV | 0x01845028 | write 1, wait for 0 (before running new code) | hardware | `cbuild/forth/reload.c` |
| L1DWB | 0x01845040 | launcher: L1D write-back | hardware | `cbuild/launcher/jump.asm` |
| L1DWBINV | 0x01845044 | write 1, wait for 0 (around DMA and code copies) | hardware | `cbuild/forth/reload.c`, `cbuild/forth/sd.c` |
| MAR192, 193, 195 | | set at start-up: DDR is cacheable in L1D | hardware | — |

## Memory our code uses

| Region | Use | Valid when | Confidence | Where |
| --- | --- | --- | --- | --- |
| 0x11800000–0x1182C000 | App image plus uninitialised data | app owns the machine | hardware | `cbuild/launcher/app.h` |
| up to 0x11830000 | App stack (top) | app | hardware | `cbuild/forth/l2.cmd` |
| 0xC0050000–0xC0250000 | Boot loader's buffer: not zero at start-up; idle while stock 1.17 runs | stock 1.17 | hardware | [6](06-quirks.md) |
| 0xC0050000, 0xC0060000–0xC0061FFF | REVERSE buffers, header, info, log | patched stock | hardware | `tools/revmenu.py` |
| 0xC0062000 | Patch variables | patched stock | hardware | `tools/menudump.py` |
| 0xC0070000 | REVERSE code (copied from the flash padding) | patched stock | hardware | `cbuild/r8.cmd` |
| 0xC0072000–0xC0080000 | App launcher code | patched stock | hardware | `cbuild/launcher/launch.cmd` |
| 0xC0080000 (≤ 0x30000) | `APP.BIN` staging before take-over | patched stock | hardware | `cbuild/launcher/app.h` |
| 0xC0100000–0xC0110000 | `UTILMENU.SYS` | inside stock 1.17 | hardware | `cbuild/utils/menu.cmd` |
| 0xC0110000–0xC0240000 | Utilities (R8UT) | inside stock 1.17 | hardware | `cbuild/utils/util.cmd` |
| 0xC0100000, 0xC0200000 | SD test buffers (`SD-TEST` in the core; `SD-RCMP`, `SD-WCMP` in the Forth app) | app | hardware | `cbuild/forth/core.c`, `cbuild/forth/main.c` |
| 0xC0300000 | `RELOAD` staging | app | hardware | `cbuild/forth/reload.c` |
| 0xC03F0000 | `RELOAD` trampoline | app | hardware | same |
| 0xC03FFF00 | Launch info: `R8LP` + path of the app the chooser started | app | hardware | `cbuild/forth/reload.h` |
| 0xC0400000–0xC4000000 | Looper slots in the sampler (15 MB per track), Blippoo recording, PADTEST and POWERLOG logs | app | hardware | `cbuild/sampler/looper.c`, `cbuild/blippoo/main.c`, `cbuild/padtest/main.c`, `cbuild/powerlog/main.c` |

## Register values: stock and ours

Stock values were read from the running stock firmware (`data/PERIPH.BIN`, a capture from our unit that is not included here). Transmit values are in `cbuild/synth/stockstate.h`; receive values are defined in `cbuild/sampler/looper.c`, `cbuild/forth/aio.c` and `cbuild/blippoo/main.c`. All are confirmed on hardware in the sense that audio works with them.

| Register | Stock | Synth (`synth/audio.c`) | PASS (`forth/aio.c`) | Looper and Blippoo |
| --- | --- | --- | --- | --- |
| PFUNC | 0x0000FFE9 | stock | stock | stock |
| PDIR | 0x14000014 | stock | stock | stock |
| XMASK / RMASK | 0xFFFFFF00 | stock / not set | stock | stock |
| XFMT | 0x000180F0 | 0x000180F8 (XBUSEL: CPU writes XBUF) | 0x000180F8 | 0x000180F0 (data port) |
| RFMT | 0x000180F0 | not set | 0x000180F8 (RBUSEL) | 0x000180F0 |
| AFSXCTL / AFSRCTL | 0x113 / 0x113 | stock / not set | stock | stock |
| ACLKXCTL | 0xA3 | stock | stock | stock |
| ACLKRCTL | 0x2F | not set | stock | stock |
| AHCLKXCTL | 0x4000 (master clock in) | stock | stock | stock |
| AHCLKRCTL | 0x0000 | not set | stock | stock |
| XTDM / RTDM | 0x3 / 0x3 | stock / not set | stock | stock |
| XCLKCHK / RCLKCHK | 0x02FF0008 | stock / not set | stock | stock |
| SRCTL1 | 0x0E (receive) | 0 | stock | stock |
| SRCTL2, SRCTL4 | 0x0D (transmit) | stock | stock | stock |
| XEVTCTL / REVTCTL | 0 (EDMA serves the data port) | 1 / not set | 1 / 1 | 0 / 0 (required with the FIFO) |
| WFIFOCTL | 0x1004 (off) | not set | not set | 0x1002, then 0x11002 (16 words per event, 2 serializers, enabled); 0x1002 to stop |
| RFIFOCTL | 0x1004 (off) | not set | not set | 0x0101, then 0x10101 (1 word per event, 1 serializer, enabled); 0x0101 to stop |
| Data path | EDMA CC0 events 0/1, 8-frame ping-pong, interrupt every 8 frames | CPU writes XBUF2/4, polled | CPU XBUF/RBUF, polled | Timer64P1 INT15 at 8 kHz, 4-frame blocks through the data port |

Start-up order we use: GBLCTL/RGBLCTL/XGBLCTL = 0, set formats, then raise HCLKRST and CLKRST, clear status (0xFFFF), raise SRCLR, prime the buffer or FIFO with silence, raise SMRST and FRST, waiting up to 5 ms for each bit to read back in GBLCTL. After a transmit underrun, drop XSRCLR/XSMRST/XFRST while keeping XHCLKRST and XCLKRST, and start again ([6](06-quirks.md)).

Other values that differ from stock:

| Register | Stock | Ours | Why | Confidence |
| --- | --- | --- | --- | --- |
| Timer64P1 PRD12 / TGCR | 1 kHz tick / 5 | 8 kHz (2999) / bit 0 set on the existing value | audio and panel polling in our apps | hardware |
| CFGCHIP2 | 0x0932 | 0x4932 | the R8 never reports VBUS; force device mode | hardware |
| MMCCMD transfers | one CMD18/CMD25 per request, any length; no DCLR or PPLEN; DMATRIG set (firmware) | runs of up to 128 blocks; DMATRIG set (since 2026-11-14; before: 7 blocks, DCLR and PPLEN) | without DMATRIG, runs of 4 KB or more fail (hardware) | hardware (ours) / firmware (stock) |
| MMCCLK | `0x200` after a CMD6 high-speed switch, CLKRT 3 (22.5 MHz) otherwise (firmware) | CLKRT 3 (22.5 MHz; 18 MHz before 2026-11-14) | we have not tried the CMD6 high-speed switch | firmware / hardware |
| MMCSD1 interrupts / DMA | interrupt-driven; data phase by DMA through a function table (firmware) | MMCIM = 0, polled; EDMA reads on CC1 experimental | simplicity | firmware / hardware |
