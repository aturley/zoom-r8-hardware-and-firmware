# 5. Hardware, subsystem by subsystem

Register names follow TI's C6748 documentation. "Stock" values were read from the running stock firmware with a small dump program (section 9). Each part ends with what we don't know; [Open questions](12-open-questions.md) collects them.

## The DSP, memory and clocks

- **Processor:** a TI C674x DSP, C6748 class, no ARM core. 360 MHz (PLL0 × 30 ÷ 2 from a 24 MHz crystal), confirmed by timing loops and the timestamp counter.
- **Memory:**

  | Region | Address | Notes |
  | --- | --- | --- |
  | L2 RAM | 0x11800000 | 256 KB; stock fast code, the vector table at 0x11813C00. Our apps load here. |
  | L1D RAM | 0x11F00000 | some stock data, including the audio buffers (below) |
  | DDR2 | 0xC0000000 | 64 MB (aliases every 64 MB); cacheable in L1D |

  Stock firmware: `.text` at 0xC0715F40, `.const` at 0xC0604668, data page pointer B14 = 0xC0812B20. R8BOOT runs from 0xC23xxxxx. The boot loader uses DDR 0xC0050000–0xC0250000 as a buffer, so it is not zero at start-up.
- **Caches:** L1D is a 16 KB cache and L1P 32 KB; L2 is all SRAM. MAR192, 193 and 195 are set, so DDR is cacheable (read from the running unit).
- **Free memory while the stock firmware runs:** stock leaves the boot loader's buffer idle. We load code at 0xC0100000–0xC0240000 and call it from the stock menu (section 7); nothing has disturbed it so far (hardware). An app that has taken over the machine can use any DDR it likes; the Blippoo app records into 0xC0400000–0xC4000000.
- **Power:** the POWER switch is read on GP8[12]; firmware holds the power on with GP7[0] and powers off by clearing it. Every custom firmware must poll the switch and do this, or the unit cannot be turned off.
- **Interrupts:** the stock vector table is at 0x11813C00. INT4, 7, 10 and 15 go to the SYS/BIOS dispatcher (firmware). Timer64P1's event (40) is already routed to INT15 by the stock interrupt mux; our apps keep that routing.
- **Timer64P1** (0x01C21000): stock runs its lower half (TIM12) at 1 kHz; our apps run it at 8 kHz for audio. It raises its event even with INTCTLSTAT.PRDINTEN12 = 0 (hardware).
- **Reset:** no watchdog is running; standalone code that services nothing has never reset. To restart into the stock firmware, arm Timer64P1 as a watchdog and let it expire: TCR = 0, TGCR = 0 then 0xB, counters 0, PRD12 = 0x6000, PRD34 = 0, then WDTCR = 0xA5C64000 and 0xDA7E4000. It fires within about a millisecond and the chip goes through the boot ROM and R8BOOT exactly as at power-on (hardware). The PLL controller's software reset (RSCTRL, 0x01C110E8) reads as locked and the unlock key does nothing.

## The fader law

The stock firmware turns a fader position (0–127) into gain with a 128-entry table (`0xC0700A88` in 1.17; 16-bit, 8192 = unity): 0 = silence, 1 ≈ −60 dB, 6 = −40, 14 = −30, 35 = −20, 59 = −10, **100 = 0 dB**, **127 = +12 dB**, roughly straight lines in dB between those points. This matches the +12 dB printed at the top of the faders. Our looper reproduces it from those breakpoints (within 0.4 dB).

## The panel processor link (UART2)

A separate processor handles keys, faders, the wheel, pads, the footswitch and the panel LEDs, and talks to the DSP over UART2 (0x01D0D000) at 166,666 baud. Stock opens it with 16× oversampling, FIFOs on, 8N1. Its pins are in PINMUX4, which depends on the board revision; leave it as R8BOOT set it. Protocol: [Appendix A](appendix-a-panel-protocol.md).

What the panel reports (all hardware):

- **Keys:** 38 keys and the REPEAT/STOP pad, which is wired as a key, on the link; one more key ID (`86`) has no known control. TEMPO is not on the link (DSP GPIO, below).
- **Faders:** 7-bit positions, sent only while moving. A snapshot request returns all nine at once; send it at start-up, or a fader that never moves is never known.
- **Wheel:** relative clicks.
- **Pads:** eight velocity pads, one message per strike and per release. There is no pressure or aftertouch.
- **Footswitch:** a 14-bit analog value; the press threshold is ours to choose.

For firmware: parse the link from an interrupt or a fast loop, so bytes are not lost while the main loop is busy (the sampler and Blippoo read it from their audio timer interrupt; the plain Forth app, which has no audio running, polls it from its main loop). Send LED messages from the main loop, not an interrupt: one 12-byte message occupies the UART for about 0.7 ms.

Unknown: what the panel processor's other commands do, the meaning of `C2`, `E2` and `E3`, and key ID 86.

## LCD

A 128×64 monochrome module with an ST7565-class controller, driven by the DSP over SPI1 (10 MHz, clock polarity 1, phase 0, 8-bit, MSB first). Chip select GP2[14], command/data (A0) GP8[9], reset GP8[8]. The stock init sequence is `AE A2 A0 C0 26 81 0D A6 A4 40 B0 10 00` with timed power-up steps; contrast is the `81 0D` pair. Frame format: 8 pages of 128 bytes, each byte 8 vertical pixels, bit 0 at the top.

**Backlight** (firmware, stock 1.17; pin levels confirmed on hardware 2026-11-13). Two DSP GPIO outputs, both set high at power-on: GP7[14] and GP7[15]. The TOOL > SYSTEM > LIGHT setting (stored at `0xC05B6098`) drives them:

| LIGHT | Stored | GP7[14], GP7[15] | Timeout |
| --- | --- | --- | --- |
| Off | 0 | both low | none |
| On (default) | 1 | both high | none |
| 15sec | 2 | both high | 15000 counts, then GP7[15] goes low |
| 30sec | 3 | both high | 30000 counts, then GP7[15] goes low |

The timeout clears only GP7[15], and the manual (p. 109) says the backlight "darkens" rather than turns off. On hardware (2026-11-13, from the Forth app, the user watching): both pins high = full brightness; either pin alone = the same dim level; both low = off. So the two pins look like two equal halves of the backlight, and the timeout dims to half. Any UI event other than a `0x4000xxxx` system event (keys, the wheel) reapplies the setting and restarts the countdown. The counts are probably milliseconds; the tick is a timer callback we did not trace to its source. Power source and battery state do not enter into it. While an operating mode word (`B14[771]`) is 2, the setting is not applied; what that mode is was not traced.

**Contrast** (firmware). TOOL > SYSTEM > CONTRAST runs 1–8 (default 5) and is stored as 0–7 at `0xC05B6094`. A table at `0xC0713BE0` turns it into the ST7565 electronic-volume value, sent as `81 vv` (masked to 6 bits): 13, 14, 15, 16, 18, 20, 22, 25. The default 5 is therefore `81 12`. The init sequence sends `81 0D` (setting 1) before the saved setting is applied. The resistor ratio (`26`) and bias (`A2`) are fixed in the init sequence.

On our unit (hardware, 2026-11-13; the user turned the wheel, mapped to `81 vv` from the Forth app): the screen looked acceptable from 11 to 21 and best at 18, stock's default. Stock settings 1–6 (13–20) all fall in that range and look much alike; setting 7 (22) is just too dark and setting 8 (25) clearly so. The full 0–63 range changes the screen strongly, so a custom firmware can use `81 vv` directly, ideally keeping the user's stock setting. Other units' panels may differ.

## Audio: McASP0 and the codec

- **Format:** I2S, two 32-bit slots per frame, 24-bit samples left-justified (XMASK/RMASK 0xFFFFFF00), MSB first, 1-bit delay. Stock AFSXCTL 0x113 (word-length frame sync, internal, active low), ACLKXCTL 0xA3 (bit clock = AHCLKX ÷ 4, receiver synchronous), AHCLKXCTL 0x4000 (master clock in from outside).
- **Serializers:** 1 receives (the ADC: input 1 = left, input 2 = right). Serializer 4 drives the line output and serializer 2 the headphones. They are independent: different signals on each were heard only on their own jack (hardware, 2026-11-12). PFUNC 0xFFE9, PDIR 0x14000014. Our apps send the same signal to both.
- **Stock data path:** EDMA3 CC0 events 0 (receive) and 1 (transmit), 8-frame ping-pong buffers in L1D, the AFIFO off, a completion interrupt every 8 received frames (about 5.5 kHz). The transmit DMA moves one word for serializer 2 and one for serializer 4 per slot.
- **Our data path:** the AFIFO on (64 words each way), fed and drained through the data port 0x01D02000 from a timer interrupt at 8 kHz. See [Hardware quirks](06-quirks.md) for the underrun stall, the XEVTCTL rule and channel alignment.
- **Measured:** about 70 dB channel separation, harmonics −69 to −95 dB, THD+N about −53 dB (limited by our test interface). Line out looped into the inputs: −1 to −2.5 dB.

## The stock audio interrupt

Code running inside the stock firmware (section 7) can play and record without touching the McASP, by joining the stock audio interrupt. All of this is stock 1.17, read from the firmware and confirmed on hardware with test utilities on 2026-11-12, unless marked.

- **The handler** (`0x1180E738`, in L2) runs once per 8 frames, also while a menu is on screen. It works out which half of the ping-pong buffers is free from the transmit PaRAM's source address and stores it in the **half flag** at `0xC08137F4` (B14 + 4 × 821): 1 means the DMA is in the first half, so this call fills the second.
- **Buffers** (L1D SRAM, so no cache maintenance is needed). Samples are 24-bit, left-justified in 32-bit words. With h = the half flag and f = 0–7 the frame:

  | Buffer | Address of frame f | Words per frame |
  | --- | --- | --- |
  | Input | `0x11F02828 + 8 × (8h + f)` | 2: input 1 (left), input 2 (right) |
  | Output | `0x11F028A8 + 16 × (8h + f)` | 4: words 0 and 2 = headphones (serializer 2), words 1 and 3 = line out (serializer 4) |

  That word 0 and 1 are the left channel and 2 and 3 the right follows from the slot order; we tested each pair with the same tone on both, so the left/right order of the outputs is inferred.
- **Per-block pointers** at `0x11F02E68` and `0x11F02E6C` are called before the frames are processed, and `0x11F02E70` and `0x11F02E74` after. A chain of further pointers from `0x11F02E78` does the per-frame processing. The pointers are set up per sample rate. In the mode we saw, `0x11F02E74` holds a bare return.
- **Hooking it:** save the pointer at `0x11F02E74`, store your own function there, and in it call the original, then read this block's input frames and overwrite its output frames. Put the pointer back before you return to the menu. Nothing in the firmware is patched.
- **Latency:** 47.3 samples (1.07 ms) from an output frame written in one call to the same sound appearing in the input frames read by the same hook, line out looped into the inputs. A re-amp recording aligned by 47 frames lined up with its source within 0.8 sample.

Unknown: what the stock per-frame chain does entry by entry, how it routes the inputs to the outputs, and the pointer values at 48 kHz.

## The audio master clock

The master clock is 512 × the sample rate (22.5792 MHz at 44.1 kHz). How it is made depends on the board ID, which the stock firmware reads at boot from three strap pins: GP0[7] | GP1[5] << 1 | GP1[4] << 2 (GPIO IN_DATA01 at 0x01E26020).

- **Board 7 (our unit):** a fractional-N PLL on I2C0 at address 0x54, output = 25 MHz × (N + F/2¹⁸) / 72. Stock writes register 0xF7 ← `10` (stop), then register 0xF5 ← `01 0E 00 00 05 3B 72 N F0 F1 F2`, with F a signed 18-bit value, low byte first. 44.1 kHz: N = 0x41, F = +7365. 48 kHz: N = 0x47, F = −57965. One step of F is about 0.06 ppm. Switching works live; the McASP set-up doesn't change.
- **Other boards:** two oscillators. 44.1 kHz family: clear GP0[9], wait 2 ms, set GP1[15]. 48 kHz family: clear GP1[15], wait 2 ms, set GP0[9]. Then wait about 35 ms.
- **I2C0** (0x01C22000) is left by the boot loader as master at about 100 kHz (ICPSC 2, ICCLKL/H 35, ICMDR 0x4220). See the STOP quirk in section 6.

## SD card (MMCSD1)

MMCSD1 at 0x01E1B000 (PSC1 module 18), 4-bit bus. The controller clock is 180 MHz; card clock = 180 MHz / (2 × (CLKRT + 1)): 400 kHz for identification, 22.5 MHz after (CLKRT 3, as stock for a card without high-speed mode; 18 MHz before 2026-11-14). Multi-block transfers of 4 KB or more need MMCCMD's DMATRIG bit (bit 16), as stock sets it; without it they fail (section 6; confirmed on hardware for reads and writes). EDMA: receive is CC1 event 28. Stock (firmware) also switches capable cards to high-speed mode with CMD6 and sets MMCCLK = `0x200`, sends each request as one CMD18/CMD25, and moves the data with DMA; in stock CARD READER mode a Mac reads at 9.3–9.4 MB/s over USB (hardware). See section 6.

Rates with our polled driver (hardware): single blocks 1.68 MB/s. Until 2026-11-14 the driver used runs of 7 blocks without DMATRIG: 3.45 MB/s. It now uses DMATRIG and runs of up to 128 blocks at 22.5 MHz: reads 10.3 MB/s and writes 5.3–5.7 MB/s (timed from Forth with `TICKS`). At 18 MHz the same driver read 8.3 and wrote 6.0 MB/s, so writes seem to be limited by the card's programming time (inferred). Through our FAT code, a new 17 MB file took about 15 s with the old driver (1.1 MB/s); with the new one Blippoo saves a 60 s take (about 16 MB) in about 5 s (about 3 MB/s). Where the rest of that time goes is not measured.

The card's MBR partition entry on our card claims 1024 blocks more than the card has; report the size from the card's CSD register, not the partition table, when exposing the card over USB.

### The stock file layer

Code running inside the stock firmware can use Zoom's file API rather than its own SD and FAT code (addresses in section 4 and appendix B). What we learned using it (hardware unless marked):

- **Create** is open mode 0x101 with attribute 0x80. It accepts long names with spaces, `+` and `%` (for example `Long name test tone -50%.wav`), and the card stayed clean under `fsck_msdos`.
- **Seek and rewrite** works: a utility wrote a WAV, then went back to offset 0 and rewrote the header with the final length.
- **Dates:** files the R8 writes are dated 2011-01-01; the clock is not set.
- **Listing a folder:** find-first (pattern, attribute mask 0x17, a 0x160-byte structure), find-next, find-close; −10406 means no more entries. `.` and `..` come first, then entries in directory order. In the structure: +0x12 the 8.3 name, +0x1F the long name (first byte 0 if none, with stale bytes after it), +0x11F the attributes (0x10 = folder), +0x120 the size, +0x124 the creation date and time, +0x138 the modification date and time (day, month, 16-bit year, two bytes, hour, minute, second). The size and attribute offsets were checked against a raw dump; the meaning of the two date stamps is our reading.
- **The pattern is matched the DOS way.** `*.*` only matches names that contain a dot, so it leaves out most folders. Use `dir\*` to list everything.
- **Hidden entries:** a Mac's `.Spotlight-V100` and `.Trashes` folders appear as `SPOTLI~1` and `TRASH~16` with attributes 0x12; a file picker should hide hidden and system entries.

Our own FAT32 code (`cbuild/forth/fat.c`, used by every app) is more limited. It reads whole files, or any part of one through an extent map (`fat_map`, `fat_read_some`, `fat_read_at`; since 15 November), lists folders with long names (`fat_next`, resumable from any saved position), and creates new files and folders (`fat_create`, `fat_mkdir`) from free clusters and an unused directory slot. It never changes or deletes existing entries, and one `fat_create` call takes at most 8192 clusters (32 MB with 4 KB clusters). For long recordings there is a streaming writer (`fat_wopen`, `fat_wwrite`, `fat_wpatch`, `fat_wclose`): every call is bounded (one write command of up to 128 blocks, or one FAT block), it returns `FAT_AGAIN` instead of waiting while the card programs, and nothing reaches the FAT or the directory until close, so an interrupted recording leaves only free-looking clusters. The SD driver underneath splits a write into `sd_write_start` and a cheap `sd_busy` poll (since 15 November).

## USB (USB0)

A USB 2.0 OTG controller (Mentor MUSB-style) used in device mode. VBUS is never detected, so it runs in forced device mode (section 6). Our apps built on the Forth core present a composite device: CDC-ACM serial on endpoints 1–3 and bulk-only mass storage (the SD card) on endpoint 4. They run at full speed (12 Mb/s) and reach about 680 KB/s with 64-byte programmed I/O; stock enumerates at high speed (480 Mb/s), which we haven't done. The controller's own DMA (CPPI, not EDMA) would be faster; we haven't used it. The stock firmware also implements USB audio and MIDI; we have not looked at those.

## Power: USB or batteries, and the battery level

The R8 runs from USB power or four AA batteries; there is no other power jack.

- **GP6[1] is high while USB power is present** (hardware, one run: it fell when USB was unplugged and rose when it was plugged back in; firmware: stock samples it through its GPIO read helper and only accepts a change after three equal samples). With USB present, the batteries do not seem to be used: adding them changed `C1` only for a moment.
- **`C1` is the supply voltage** (appendix A). Stock converts it as mV = C1 × 19800 / 1024 + 457 (firmware, 1.17 at 0xC07BEC38; the raw value is kept at B14[612]). Seen: about 249 on USB (≈5.27 V) and 327 on fresh alkaline batteries (≈6.78 V).
- **Battery level and low battery (firmware, 1.17 around 0xC079F408).** Only checked on batteries (GP6[1] low) and after start-up. Level 0–4 = how many of four thresholds `C1` has reached. The thresholds come from a table at 0xC0707FE0, by battery type (the BATTERY setting at 0xC05B60C8: 0 = Alkaline, 1 = Ni-MH, confirmed on hardware with the BATTINFO utility) and by the current level, which gives hysteresis:

| Type | Thresholds rising (C1) | ≈ volts | Falling (lower) once at a higher level |
| --- | --- | --- | --- |
| Alkaline | 203, 235, 263, 273 | 4.38, 5.00, 5.54, 5.74 | 213, 234, 262 |
| Ni-MH | 228, 248, 253, 256 | 4.87, 5.25, 5.35, 5.41 | 233, 247, 252 |

  At level 1 for more than 4 s stock posts event `0x40000009`, repeated every 4 s; at level 0 it posts `0x4000000A`, up to 10 times. `0x4000000A` is also what stock's power-switch reader posts when the switch goes off, so an empty battery is handled like turning the R8 off (firmware, matched by event code). That `0x40000009` is the "Low Battery!" warning is inferred, not traced.
- **The setting in an app:** stock keeps the BATTERY setting at 0xC05B60C8, in DDR an app is free to reuse. So it is only reliable straight after stock ran: our chooser copies it to 0xC03FFFE0 (`R8BT`, value) and apps read the copy (`cbuild/synth/battery.c`). Before 14 November the apps read stock's word at start-up, which an earlier app's buffers could already have overwritten.
- **For your own firmware:** on batteries (GP6[1] low), treat alkaline `C1` below about 213 as low and below 203 as empty (Ni-MH: 233 and 228), save your work and switch off (power hold GP7[0] low).

## Input switches and phantom power

- **Which switches the DSP sees** (hardware, POWERLOG 2026-11-13): GP8[11] is the PHANTOM switch and GP8[10] the METRONOME output switch. The input 1 selector (Hi-Z / MIC LINE / built-in) and the input 2 selector changed no GPIO and no panel message, so they are analog only.
- **The PHANTOM switch is only an input.** The DSP decides whether phantom power is on (firmware, stock 1.17). On a change of GP8[11], stock's handler (0xC079F5D8) drives **GP7[10]** to match: 1 = on (event `0x10104`), 0 = off (event `0x20104`). Turning on goes through 0xC0771A60: wait 5 ticks, set GP7[10], wait 5 ticks. The current state is cached at B14[773]. The handler skips the pin, but still posts the event, when B14[771] is 2. B14[771] is a system mode we have not identified; the setters we found write 0 or 1.
- **Voltage: GP7[8], high = 48 V, low = 24 V** (firmware). TOOL > SYSTEM > PHANTOM offers "48V" and "24V" (menu record 0xC06E25CC, value list 0xC0715AF8). The choice is stored at 0xC05B609C, in the same settings block as BATTERY, with 0 = 48 V (the default) and 1 = 24 V. The function 0xC0771AAC writes GP7[8] = 1 − setting.
- **When stock writes these pins** (firmware):
  - *At start-up* (around 0xC07E1F2C): it sets GP7[8] from the stored setting, waits 10 ticks, then reads the switch and sets GP7[10] to match.
  - *On a change of the setting* (menu handler 0xC07ED8EE): GP7[10] off, GP7[8] set, wait 100 ticks, then GP7[10] from the switch again. Phantom is never switched between voltages while on.
  - *At shutdown* (0xC07EB5F4): it clears GP7[10] before cutting the power.

  The tick is probably 1 ms (not checked).
- **Seen on our unit:** while POWERLOG ran (launched from stock, at 48 V), bank 7 read GP7[8] = 1 and GP7[10] = 0 throughout. That includes a snapshot taken while GP8[11] read 1. So flipping the switch does nothing in an app that doesn't copy stock's logic (hardware).
- **That GP7[10] switches the phantom supply on and off is inferred** from how stock drives it. It has not been measured: a DC voltmeter between pins 2 and 1 of an XLR input would confirm it.
- **For your own firmware:**
  - An app launched from stock keeps the phantom state stock had at launch, whatever the switch does later.
  - A standalone image that never sets these pins as outputs leaves them in their reset state (inputs), so phantom then depends on the board's pull resistors, which are unknown.
  - To behave like stock:
    - poll GP8[11] (debounced);
    - drive GP7[10] from it;
    - set GP7[8] from the user's setting (0xC05B609C while stock's RAM is intact; where it is stored in flash we haven't found);
    - turn GP7[10] off before changing GP7[8] and before switching off.

## DSP GPIO used by the R8

| Pin | Direction | Use | Confidence |
| --- | --- | --- | --- |
| GP0[7], GP1[5], GP1[4] | in | board ID straps | firmware + hardware (reads 7) |
| GP0[9], GP1[15] | out | oscillator enables (boards other than 7) | firmware |
| GP1[6] | out | SPI flash chip select | firmware |
| GP2[14] | out | LCD chip select | hardware |
| GP6[0] | in | TEMPO key, active low | hardware |
| GP6[5] | in | update key at power-on (R8SYS's check) | firmware |
| GP6[10], GP6[11] | in | SD card write protect, card detect | firmware |
| GP7[0] | out | power hold | hardware |
| GP7[8] | out | phantom voltage: 1 = 48 V, 0 = 24 V | firmware (pin level seen on hardware) |
| GP7[9], GP7[11], GP7[12] | out | TEMPO LED, PEAK 1, PEAK 2 (active high) | hardware |
| GP7[10] | out | phantom power on (driven from the PHANTOM switch by software) | firmware; effect on the supply inferred |
| GP7[14], GP7[15] | out | LCD backlight: both high = full, either one alone = dim (the same level), both low = off | hardware (levels), firmware (use) |
| GP8[8], GP8[9] | out | LCD reset, LCD command/data | hardware |
| GP8[10] | in | METRONOME output switch | hardware |
| GP8[11] | in | PHANTOM switch, 1 = on | hardware (pin), firmware (polarity) |
| GP8[12] | in | POWER switch | hardware |

GPIO registers per bank pair are at 0x01E26010 + 0x28 × pair: +0 DIR, +4 OUT, +8 SET, +0xC CLR, +0x10 IN.

## EDMA3

Two channel controllers: CC0 at 0x01C00000 (stock uses it for audio, I2C and SPI) and CC1 at 0x01E30000 (SPI, and MMCSD1 events 28/29). The stock completion interrupt for CC0 (event 8) is routed to DSP interrupt 7.

Stock audio uses PaRAM set 1 (transmit, linked ping-pong through sets 64 and 65, 8 bytes per slot, 16 slots per half) and set 0 (receive, sets 66 and 67, 4 bytes per slot), through the McASP data port with the McASP's FIFO off. Only the receive side raises an interrupt. The stock SD driver moves the bulk of each transfer by DMA through CC1, with the CPU handling the head and tail (firmware; see the SD section).

Our apps use no EDMA. Their audio runs from a timer interrupt through the McASP FIFO at about 7–8% CPU (looper) with no underruns, even during USB storage; EDMA for SD reads gave the same speed as the CPU. DMA would pay off only to overlap transfers with other work. MMCSD1 transmit as CC1 event 29 is presumed, not checked.
