# 4. Stock firmware

All addresses are for stock 1.17.

## The update file: R8SYS.BIN

R8SYS.BIN (1.17: 7,169,652 bytes) is a 64-byte Zoom header followed by a standard TI AIS (Application Image Script) boot script, the format the C6748's boot ROM understands. It is **not compressed, not encrypted and not signed**. The only integrity check is a 32-bit byte sum.

| Offset | Contents |
| --- | --- |
| 0x00 | Tag `ZOOM/D224` (the updater checks it) |
| 0x20 | Version string, `1.17` |
| 0x30 | `0x00000001` (purpose unknown) |
| 0x34 | Checksum: 32-bit sum of every byte from 0x40 to the end of the file |
| 0x40 | AIS script, magic `0x41504954` |
| 0x2126E8 | 0xFF padding (about 55 KB; useful as space for added code, see below) |
| 0x220040 | About 4.7 MB of rhythm samples (16-bit PCM) |

The AIS script enables sequential reads, runs two boot-ROM functions to set up the PLL (360 MHz CPU from a 24 MHz crystal) and DDR2, loads eight sections, and jumps to `_c_int00` at `0xC07FAB00`. A modified file needs only its checksum recomputed. Our `r8fw.py fixsum` does that.

A standalone firmware can reuse the header and the PLL and DDR commands byte for byte, then load its own sections. Ours load into the DSP's internal L2 RAM at `0x11800000`, where the stock firmware also keeps fast code.

The updater recognizes five files: `R8SYS.BIN` (this image), `R8BOOT.BIN` (the boot loader), `R8SUB.BIN` (the panel processor, labelled SUBSYS), `R8TEST.BIN` and `RHYTHM.BIN` (the rhythm samples, labelled WAVE).

## Flash layout and the update sequence

See the table in [Safety and recovery](02-safety-and-recovery.md). The file maps one to one onto the SYSTEM region: file offset x goes to flash 0x80000 + x − 0x40.

The updater (`0xC07AF808` in R8SYS; R8BOOT contains the same code):

1. Erases the marker sector at 0x7A0000, invalidating the `ZOOM/R8` marker.
2. Erases the target region.
3. Programs it in chunks from the file.
4. Reads it back and compares with the file.
5. Rewrites the marker.

So the marker is valid only after a complete, verified write.

## Boot

1. The DSP's boot ROM loads R8BOOT (an AIS image at flash 0) into DDR and runs it.
2. R8BOOT sets up the hardware and the panel link, and reads the held keys as reported by the panel processor.
3. With no special keys and a valid marker, it shows the boot screen, loads R8SYS from flash with its own AIS interpreter and jumps to it.
4. Otherwise (marker missing, or a recovery key pattern such as PLAY held) it runs its own updater, without running R8SYS.

R8SYS also checks a "hold at power-on" key on GPIO GP6[5] and has its own copy of the updater. In our tests the PLAY path was always handled by R8BOOT.

## Inside 1.17

- **Operating system:** TI SYS/BIOS 6, with ITRON-style object names (`TSKID_`, `SEMID_` and so on). 21 named tasks plus an init task; the task table is at `0xC035D850` (22 entries of 68 bytes, entry function at +40). Task names come from SYS/BIOS's text table, which survives in the binary.
- **Main tasks** (entry addresses):

  | Task | Entry | Task | Entry |
  | --- | --- | --- | --- |
  | MainMenu (really the LCD hardware task) | 0xC07CCD78 | LCD | 0xC07DDA98 |
  | Panel | 0xC079E020 | AudioCmdRcv (panel link receive) | 0xC07BBD38 |
  | SerialHandler | 0xC07B4CA8 | Pad (also polls DSP input pins) | 0xC079F770 |
  | SDriver | 0xC0792800 | RhyHdr | 0xC074C198 |
  | EfxQFuncs | 0xC073C9F4 | ARRM | 0xC07E8D14 |
  | RecApi | 0xC077BF50 | TransportManager | 0xC07E5FEC |
  | ZStream_IO / ZStream | 0xC078234C / 0xC0782004 | Tuner | 0xC07CA06C |
  | TimeStretch (in L2) | 0x11804600 | AIF_ManageTask (USB audio) | 0xC07EE568 |
  | UsbMidiRxTask / UsbMidiTxTask | 0xC07D5F90 / 0xC07D62EC | AudioClkMng | 0xC07F3324 |

- **Drivers** sit in recognizable clusters: UART `0xC07D4C04`–`0xC07D5600`, McASP0 `0xC07A60E0`, SD/MMC `0xC07A7550`–`0xC07A8844`, SPI `0xC07D92D0`, I2C0 `0xC07E3320`/`0xC07E3564`, GPIO `0xC07F2220`–`0xC07F2380`, USB0 `0xC076AC14`–`0xC076E0D8`, EDMA3 CC0/CC1 `0xC07E4040`/`0xC07E51C0`. Peripheral base pointers are globals set from the `.cinit` table and reached through the data page pointer B14 (`0xC0812B20`).
- **Events:** panel input becomes events posted to the tasks. A key with ID n posts `0x10000 | n` on press and `0x20000 | n` on release (confirmed on hardware for ENTER, EXIT and STOP); `0x4xxxx` appears to be hold (firmware). The wheel posts `0x10000000`. A key-dispatch jump table at `0xC0813E98` maps key IDs to actions, and a control-surface table at `0xC06FE848` maps every key to a Mackie Control function for DAW mode.
- **The LCD buffer chain:** drawing code writes 1-bit pixels into a 1 KB buffer at `0xC05F4178` (byte = page × 128 + column, bit 0 = top row) and marks dirty pages. `lcd_flush_pages` (`0xC0748250`) hands dirty pages to `lcd_put` (`0xC07CCF34`), which wakes the LCD hardware task to send them over SPI. A freeze counter at `0xC081311A` stops flushing; patched code used it to take over the screen and give it back.
- **Menus** are data: per-track menu descriptors and 80-byte item records (name, type, handler) in RAM. A patch can add an item by filling a free record just before the menu engine runs, or by giving a menu a longer list in the code cave. Code of your own can build whole menus and run them with the stock menu engine (section 7 describes the layout).
- **File API:** open, read, write, seek, close, delete and rename functions with FatFs-like arguments (for example `open(&fd, path, mode, …)` at `0xC0763C6C`). Patched code can use them to read and write files on the card.

## Where to put added code

For patches to stock firmware, three places worked:

- **Unused text:** SYS/BIOS's module and instance name strings (around `0xC06D9800`) are only printed on errors. About 1.3 KB was used for hooks.
- **The 0xFF padding** in the file (about 55 KB). It is written to flash but never loaded into RAM, so code there is copied in with the firmware's own flash-read routine, the caches are synchronized, and it is called with a far branch.
- **Retargeted calls:** many hooks only change the target of one existing `CALLP` instruction, leaving its arguments, which the caller sets in the same execute packet, untouched.
