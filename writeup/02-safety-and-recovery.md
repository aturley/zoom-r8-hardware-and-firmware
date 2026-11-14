# 2. Safety and recovery

Read this before flashing anything. The short version:

- The R8 has a separate first-stage boot loader (R8BOOT) with its own SD-card updater. Holding **PLAY** at power-on starts it **without running the main firmware**. This was tested on our unit and is what makes experiments reversible.
- Never write the BOOT region of the flash, and never send a sub-processor update (R8SUB.BIN). Nothing we know of recovers from a bad boot loader or a bad panel processor.
- Keep a copy of stock R8SYS.BIN for your version, and a full flash dump, before you start.
- The SD card is the other thing you can damage. Its controller corrupts multi-block transfers unless one particular bit is set (below), and a host computer and the R8 must never write to the card at the same time.

## What can and cannot brick the unit

The R8 boots from an 8 MB SPI flash:

| Flash region | Contents | Written by |
| --- | --- | --- |
| 0x000000–0x07FFFF | BOOT: R8BOOT, the first stage the DSP's ROM loads | R8BOOT.BIN |
| 0x080000–0x79FFFF | SYSTEM: the main firmware (R8SYS.BIN without its 0x40-byte header) | R8SYS.BIN, R8TEST.BIN |
| 0x2A0000 (inside SYSTEM) | WAVE: the rhythm samples | RHYTHM.BIN |
| 0x7A0000 | The `ZOOM/R8` "image valid" marker | the updater |
| 0x7B0000–0x7FFFFF | Version strings and settings | the firmware |

R8SUB.BIN goes to the panel processor, not this flash.

- **SYSTEM (R8SYS.BIN):** recoverable. A broken main firmware can be replaced from the SD card through R8BOOT, as long as R8BOOT is intact (see below).
- **BOOT (R8BOOT.BIN):** not recoverable by any means we know. If R8BOOT is broken, the unit no longer reaches any updater. We have not found the DSP's boot-mode pins, a ROM UART boot path or JTAG pads. Do not write this region.
- **Panel processor (R8SUB.BIN):** not recoverable by any means we know. R8BOOT relies on the panel processor to report the held keys that select recovery mode. Do not send it updates.

## The recovery path

R8BOOT runs first on every power-on. It talks to the panel processor, reads which keys are held, and decides:

1. **No special keys, and the `ZOOM/R8` marker is present:** load R8SYS from flash and run it (normal boot).
2. **The marker is missing** (an update was interrupted): run R8BOOT's own updater.
3. **Certain held-key patterns:** run R8BOOT's own updater or other modes, without running R8SYS.

R8BOOT reads the held keys as 8 bytes of the panel's key matrix (bit b of byte r = panel key ID 5r + b, the same IDs as appendix A; inferred from PLAY, which matches). The patterns (firmware, 1.17 boot loader; only PLAY tried on hardware):

| Keys held at power-on | Mode | What R8BOOT offers to write from the card |
| --- | --- | --- |
| PLAY | 2 | `R8SYS.BIN` to SYSTEM (also used when the marker is missing) |
| PROJECT + REW | 3 | `R8BOOT.BIN` to BOOT: **the boot loader** |
| PROJECT + FF | 4 | `R8SUB.BIN` to the panel processor |
| REW + FF + PLAY | 5 | `R8TEST.BIN` to SYSTEM |
| TRACK 7 status + REC | 6 | `RHYTHM.BIN` to WAVE |

Each mode first checks the battery ("Low Battery! / Please Power Off"). Without its file on the card it shows "No Update File" and waits until power-off. With the file, it asks for confirmation: two choices, with the right-hand one (cancel) selected at first; LEFT then ENTER writes, ENTER alone cancels ("Please Power Off") (firmware). So nothing is written without that deliberate choice. **Never leave `R8BOOT.BIN` or `R8SUB.BIN` on the card,** and keep test files from being named `R8TEST.BIN`: modes 3 and 4 are the only ways the R8 rewrites its boot loader or panel processor.

Holding **PLAY** at power-on is one of these patterns. We confirmed that R8BOOT, not R8SYS, handles it: a test build changed only R8SYS's updater message, and reflashing while holding PLAY still showed the stock message. So **a main firmware that flashes cleanly and then crashes can still be replaced**: hold PLAY at power-on with stock R8SYS.BIN on the card. We used this many times while testing standalone firmware.

An interrupted update is also safe by design: the updater erases the marker first and rewrites it only after the new image has been written and read back. A power cut in between leaves no marker, and R8BOOT goes straight to its updater.

What this does not cover: a firmware that damages the flash outside SYSTEM, or that changes the panel processor. Custom code should never call the stock flash-erase or flash-write routines on other regions.

## Before you start

1. **Keep stock R8SYS.BIN** for your firmware version on your computer.
2. **Dump the whole flash.** We used a patched stock firmware that copied the 8 MB flash to `A:\FLASH.BIN` when the VERSION screen was opened (section 4). The SYSTEM region of the dump matched R8SYS.BIN byte for byte, which is also a good check that you understand the layout.
3. **Use a card you can afford to reformat**, or back it up, until your SD code is proven.

## SD card risks

- **Multi-block transfers of 4 KB or more need MMCCMD's DMATRIG bit (bit 16).** Without it the controller corrupts them: a write gets a zero byte inserted at offset 4095, and a read loses the byte at offset 4094 and never completes. This happens whether the CPU or DMA feeds the FIFO. With DMATRIG set, as stock does, transfers of at least 128 blocks are exact in both directions (hardware, 2026-11-14). See [Hardware quirks](06-quirks.md).
- **We damaged a card's FAT once** this way: the first time a host wrote to the card through our USB storage code, before we knew about the fault. It was repaired from the FAT's backup copy. Test multi-block writes on unused blocks (we used blocks 1–8191, before the partition) before you let a computer or your own filesystem code write.
- **Write new files conservatively.** Our FAT writer only uses free clusters and an unused directory slot, and writes data, then the FAT chain, then the directory entry. An interrupted write can only leave unreferenced clusters, which a disk check reclaims; it cannot damage existing files. Check the result with a real disk checker (we used macOS `fsck_msdos` on a simulated card image, then `diskutil verifyVolume` on the real card).

## Host computer lessons

If your firmware exposes the card over USB mass storage, the computer becomes a second writer.

- **Never write to the card from the R8 while a computer has it mounted.** We tried telling the Mac the card had been removed while the R8 wrote to it, the way a card reader does. macOS did not notice in time, kept a stale view, and the episode ended with macOS refusing to mount the card until the dialogs on screen were dealt with. The safer rule, which our current code follows, is to refuse to write while the host has the card mounted. (The stock firmware avoids the question: its USB storage mode is a separate mode in which the recorder itself doesn't use the card.) Hosts announce that with the SCSI command PREVENT ALLOW MEDIUM REMOVAL, and release it with an eject.
- **Confirm the eject before restarting.** Our development loop copies a new app to the card over USB, ejects it, then restarts the app. Once, macOS's Spotlight held the volume, the eject silently failed, and the app restarted with the card still mounted read-write. Nothing was damaged, but check that the eject really happened, and retry, before disconnecting.
- **Expect desktop indexing.** macOS writes Spotlight and Trash folders to any card it mounts read-write.

## Things we have not needed, and don't know

- A watchdog: none is running. Standalone firmware that services nothing has never rebooted on its own. Our apps arm Timer64P1 as a watchdog on purpose to restart into the stock firmware (section 5).
- The DSP's boot-mode pins, ROM UART boot, and JTAG pads: unknown. These would be the only way back from a broken R8BOOT.
- Whether R8BOOT also has a recovery for a missing or damaged R8SYS beyond the marker check and the PLAY mode: none found in the boot loader (no UART or alternate boot).
