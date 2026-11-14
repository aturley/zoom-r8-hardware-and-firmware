# 12. Open questions

What we don't know yet, roughly in order of how much it matters to a firmware writer.

## Safety

- **The DSP's boot-mode pins, ROM UART boot and JTAG.** These are the only possible way back from a broken boot loader or panel processor. Finding them needs the board opened.
- **Which held keys select R8BOOT's other modes.** We know PLAY reaches its updater, and that R8BOOT recognises several key patterns (modes 2 to 6), but not which keys produce them.

## Hardware

- **Battery level in practice.** Answered from firmware and one hardware run: `C1` is the supply voltage, GP6[1] is high on USB power, and stock's battery thresholds are known (section 5). Still open: what the two low-battery events do on screen, and a full rundown on batteries to see how `C1` falls.
- **LCD backlight and contrast.** Traced in firmware (section 5): GP7[14]/[15] are the backlight and contrast is `81 vv` from an 8-entry table. The pin levels are confirmed on hardware (either pin alone = the same dim level, both = full, none = off). Contrast is confirmed too (`81 vv`; usable 11–21 on our unit, best 18 = stock default). Still to confirm: the timeout's tick length.
- **Phantom power.** Answered from hardware and firmware (section 5): GP8[11] is the PHANTOM switch and GP8[10] the METRONOME switch. Software drives GP7[10] (on) and GP7[8] (48 V / 24 V). Still open: a voltmeter check that GP7[10] really switches the supply, what the board does with the pins undriven, and the system mode (B14[771] = 2) in which stock leaves phantom alone.
- **The clock chip.** A fractional-N PLL at I2C address 0x54 on board 7 (section 5). Its part number, its lock range, and the meaning of its other setup bytes are unknown. The stock firmware asks for 88.2 and 96 kHz with the same chip settings as 44.1 and 48 kHz, so how (or whether) those rates work on this board is unclear.
- **The chips themselves.** Part numbers of the flash, DDR2, codec and panel processor would answer several questions. Our early notes name the ADC and DAC as WM8783 and WM8524, but we have not opened the unit to check.

## Panel processor

- What the commands `89`, `AC` and `AD` do. `89` with every bit on or off showed no visible effect. (`A6` turned out to be the pad lights; appendix A.)
- The analog value `C2` (always 101 so far), and the unknown bytes in the `E0` snapshot.
- The start-up replies `E2 03 00 03 03 06 01 03 00` and `E3` (8 bytes that differ between boots): which command each answers, and what they mean.
- Key ID 86: the only panel key ID with no known control. No message with it has been seen.
- Its firmware and how R8SUB.BIN updates it. We have deliberately not touched this.

## Stock firmware internals used by utilities

- **The stock audio chain.** The per-frame pointer chain in the audio interrupt (what each entry does), its set-up at 48 kHz, and how the stock mixer routes the inputs to the outputs.
- **The left/right order of the output words.** Words 0/2 are the headphones and 1/3 the line out (confirmed); that 0 and 1 are left is inferred.
- **Stock dialogs we haven't traced:** the yes/no confirmation and the name-entry screen, which file utilities such as delete, copy and rename would need.
- **How deep stock menus can nest.** Four levels work (TOOL, SD CARD, R8 UTILS, a utility's menus, plus a file picker's folder levels); we have not found the limit.

## Firmware features we haven't explored

- **USB audio interface mode** (`AIF_ManageTask` and the USB audio class).
- **USB MIDI** (the UsbMidi tasks).
- **The USB controller's own DMA engine** (faster USB storage).
- **USB high speed.** Stock enumerates at 480 Mb/s; our apps run at full speed (12 Mb/s), which now limits USB storage more than the card does.
- **The SD card's high-speed mode.** Stock switches capable cards with CMD6 and then writes MMCCLK = `0x200`; we read bit 9 as a divide-by-4 (45 MHz, inferred) and have not tried it. Ours runs at 22.5 MHz; writes did not get faster from 18 to 22.5 MHz, so the gain may be for reads only.
- **Effects and rhythm engines** in the stock firmware.

## Host interaction

- Exactly what macOS sends to a USB storage device around mounting, ejecting and a medium change. Our app's `MSC-LOG` word records the last 32 SCSI commands for this.
- **The date on new files.** Files the R8 writes are dated 2011-01-01. Whether the unit keeps a clock that firmware could set, we haven't checked.

## Answered along the way (November 2026)

- **The headphone output** is McASP serializer 2, independent of the line out on serializer 4 (hardware, section 5).
- **Pad pressure:** there is none. The pads send a strike velocity and a release, and nothing while held (hardware, appendix A).
- **Key `A7`** is the REPEAT/STOP pad below the MASTER fader, wired as a plain key. Stock handles it in the pad code: rolls on the rhythm pads, looped playback on the loop-track pads (hardware and firmware, appendix A).
- **Pad numbering:** one bit per pad, not a pad number (hardware, appendix A).
