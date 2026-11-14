# Appendix A: the panel protocol

The DSP talks to the panel processor over UART2 at 166,666 baud (stock divisor gives about 167.9 kbaud), 8N1. The panel processor reads the keys, faders, wheel, pads and footswitch, and drives the panel LEDs. It does **not** drive the LCD (the DSP does, over SPI) and does not see the TEMPO key (a DSP GPIO pin).

Confidence: *hardware* = seen on our R8; *firmware* = read from stock 1.17; *inferred* = best fit, unconfirmed.

## Framing

- **Panel → DSP:** MIDI-like. A byte with bit 7 set is a status byte; data bytes are 7-bit. There is no terminator. Faders and the wheel use running status (one status byte, then several values).
- **DSP → panel:** `cmd 01 len payload… F7`.

## Start-up

The stock firmware sends these at boot. Replaying them is enough to wake the panel processor in a standalone firmware.

| Sent | Reply |
| --- | --- |
| `84 01 01 01 F7` | none |
| `A0 01 01 02 F7` | `E3` + 8 bytes (8 nibbles; differed between boots) |
| `9E 01 00 F7` | `E0` + 22 bytes: the snapshot below |
| `81 01 00 F7` | `E1 00 00 64` |
| `83 01 00 F7` | none |
| after about 200 ms: `8A 01 01 02 F7`, `8B 01 01 00 F7` | probably `E2` (below) |

The panel also sends `E2 03 00 03 03 06 01 03 00` once at start-up (seen on hardware, 2026-11-13). It arrived about 36 ms after `8A`/`8B` were sent, and the panel processor had been running long before, so it is probably the reply to `8A` or `8B` (inferred; an earlier capture of the stock start-up showed no reply to them). Its meaning is unknown.

## Keys

Press sends `<status> 03`, release `<status> 04`, where status = 0x80 + key ID. The stock firmware posts UI event `0x10000 | ID` on press and `0x20000 | ID` on release (hardware, checked for ENTER `0x10018`, EXIT `0x10013` and STOP `0x10020`; firmware for the rest). Code that runs inside the stock firmware sees these codes (section 7).

| Status | Key | Confidence |
| --- | --- | --- |
| `80` | ON/OFF 1 (input 1) | hardware |
| `81` | EFFECT | hardware |
| `82` | USB | hardware |
| `83` | TOOL | hardware |
| `84` | PROJECT | hardware |
| `85` | ON/OFF 2 (input 2) | hardware |
| `86` | no known control (the only one left) | |
| `87` | RHYTHM | hardware |
| `88` | TRACK | hardware |
| `89` | PAN/EQ | hardware |
| `8A`–`8E` | F1 AUTO PUNCH I/O, F2 A-B REPEAT, F3 \|◀◀, F4 ▶▶\|, F5 MARK/CLEAR | hardware |
| `8F`–`92` | track 1–4 status keys | hardware |
| `93` | EXIT | hardware |
| `94`–`97` | cursor UP, DOWN, RIGHT, LEFT | hardware |
| `98` | ENTER | hardware |
| `99`–`9C` | track 5–8 status keys | hardware |
| `9D` | MASTER status key | hardware |
| `9E`, `9F` | REW, FF | hardware |
| `A0` | STOP | hardware |
| `A1`, `A2` | PLAY, REC | hardware |
| `A3`–`A6` | the four keys under the display, left to right | hardware |
| `A7` | REPEAT/STOP, the pad below the MASTER fader | hardware |

That is 38 keys and the REPEAT/STOP pad on the link (`86` has no known control). TEMPO is not on the link: it is DSP GPIO GP6[0], active low.

**REPEAT/STOP (`A7`)** sits in the pad row but is wired as a plain key: `A7 03` on press, `A7 04` on release, no velocity (hardware, 2026-11-13). The control-surface table at `0xC06FE848` has 0xFF for it, so it does nothing in control-surface mode (firmware).

Stock handles it in the pad code, not the key dispatcher (firmware, 1.17; traced 2026-11-13):

- **Path.** The panel receive task posts `0x10027` like any key. On a press it also sends message type 7 to the SerialHandler task (`0xC07B4CA8`), which also receives every pad hit. The pad code checks whether REPEAT/STOP is held with the held-key bitmap at `0xC06045D0` (test function `0xC07B6768`, bit = key ID).
- **Rhythm pads: roll.** If REPEAT/STOP is held when a drum pad is hit (the trigger is `0xC07B53EC`), the pad is stored with its velocity in a 16-slot roll table (`0xC0603150` velocity, `0xC0603130` interval, `0xC0603110` counter). A tick function (`0xC07B58F4`, called from the RhyHdr task's clock loop) counts each slot up and re-hits the pad at the stored velocity when the counter reaches the interval. A pad release clears the slot only while REPEAT/STOP is still held. If REPEAT/STOP is released first, the pad keeps rolling after it is let go. The next press of a pad whose slot is active clears the slot and returns without sounding a hit, whether or not REPEAT/STOP is held. This matches the manual (p. 50: "If you release [REPEAT/STOP] before the pad, that sound continues rolling after the pad is released. Press the pad again to stop it."). The tick's re-hits pass the pad number with bit 7 set. These skip the counter reset and the active-slot check, so a roll never cancels itself. They also reload the interval, so a PAD ROLL change applies from the next hit. All firmware.
- **Roll rate.** The interval comes from the PAD ROLL setting (index at `0xC05B6088`) through a table at `0xC0702708`: 19 records of an 8-byte label and a 32-bit tick count, from 1/32 = 6 ticks up to 16/4 = 768 (the manual's list on p. 50). That is 48 ticks per quarter note, which the manual confirms (p. 66: "1 tick (1/48 of a quarter-note)"), so the roll follows the tempo. The default is 12 ticks (1/16).
- **Loop-track pads: actions.** A loop-track pad hit becomes an action code. The code goes to the RhyHdr task as message `0x0E000000 | action << 8 | pad` (`0xC074E178`) and is queued (`0xC074A810`, ring at `0xC04CC528`). It takes effect when the queue is drained (`0xC074B2D4`).

  | Action | Sent when | Effect | Confidence |
  | --- | --- | --- | --- |
  | 2 | hit, PAD = mode 0 | starts the track at its loop start | firmware; mode 0 = Repeat inferred from the manual's order |
  | 3 | hit with REPEAT/STOP held | starts looped playback. If the track is already playing, it stops instead | firmware; matches manual p. 66 ("Press a pad while holding REPEAT/STOP to enable loop playback. Press the pad again while holding REPEAT/STOP to stop loop playback.") |
  | 4 | hit, PAD = mode 1 (Gate) | starts the track | firmware |
  | 5 | hit, PAD = mode 2 | starts the track from position 0 | firmware; mode 2 = 1Shot inferred |
  | 1 | release of a Gate pad | stops the track if its current action is 4 | firmware |

  The PAD mode per pad is at `0xC0603090`.
- **Loop-track pads: REPEAT/STOP pressed while holding a pad.** `0xC06030F0[pad]` is 1 while a pad is held: it is set on press and cleared on release. The type 7 message negates it for every held pad (`0xC07B5206`). That pad's release then skips the Gate stop and leaves the value negative. Its next press sends no action and only marks it held again. The release after that stops a Gate pad as usual. Firmware for the table logic. The effect we infer: a Gate pad keeps playing after release if REPEAT/STOP was pressed while it was held. Nothing else reads `0xC06030F0` (firmware).

Not traced: the timing of the queue drain (the manual says pad starts and stops wait for the global quantization), what `0xC06030B0` (pads the SerialHandler ignores) means, and what the rhythm-pad flags at `B14[2004]` are. Bit 0 has to be set for drum pads to sound; bit 3 diverts hits to another path, probably pattern recording.

## Faders, wheel, pads and other inputs

| Message | Meaning | Confidence |
| --- | --- | --- |
| `C3`–`CA vv` | faders 1–8, 0–127; sent about every 32 ms while moving | hardware |
| `CB vv` | MASTER fader | hardware |
| `C2 vv` | an analog value, 101 in every snapshot so far | firmware |
| `C1 hh ll` | supply voltage, 14-bit (hh × 128 + ll), sent when it changes. Stock converts it as mV = C1 × 19800 / 1024 + 457. Seen: about 249 on USB (≈5.27 V), 327 on fresh batteries (≈6.78 V). Battery thresholds: section 5 | hardware values; conversion from firmware |
| `C0 hh ll` | CONTROL IN footswitch, 14-bit analog, sent on change: pressed ≈ 0x1E, released 0x0A–0x15 (noisy) | hardware |
| `D0 dd` | wheel, relative 7-bit two's complement: `01` = one click clockwise, `7F` = one click back | hardware |
| `F0 aa bb vv` | a velocity pad: one bit per pad (below); vv = velocity, `00` = release | hardware |

Fader values reach 127 slightly before the end of travel.

## Pads

The eight velocity pads send `F0 aa bb vv`, with one bit per pad (hardware, 2026-11-13, from a log of every panel byte):

| Pad | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `aa bb` | `00 01` | `00 02` | `00 04` | `00 08` | `01 00` | `02 00` | `04 00` | `08 00` |

- **Velocity:** soft taps gave 0x08–0x0C, hard taps 0x36–0x69.
- **Release:** `vv = 00`. Every press had a release, 35–90 ms after a quick tap.
- **Two pads at once:** one message per pad (for example `F0 01 00 08`, then `F0 02 00 08` 16 ms later), and separate releases. We have not seen two bits set in one message.
- **No pressure or aftertouch.** Nothing is sent while a pad is held. Pressing a held pad harder sends nothing; easing off sends a release, and pressing again sends a new soft hit (velocity about 0x08). The pads behave as switches with a strike velocity.

**Pad lights** are a separate command, not part of `88` (hardware, 2026-11-13):

| Command | Effect |
| --- | --- |
| `A6 01 02 hi lo F7` | pad lights: v = hi × 16 + lo, one bit per pad in the same layout as the pad input (bit 0 = pad 1 … bit 7 = pad 8); 1 = lit |

Stock sends `0F 0F` from its all-LEDs-on routine and `00 00` from all-off, and the pad state while loop tracks play (0xC07BBA08 builds it). `88` with every bit set lights no pad, and REPEAT/STOP has no light.

The REPEAT/STOP pad in the same row is not a velocity pad; it sends key `A7` (above).

## Reading every fader at once

Send `9E 01 00 F7`. Within about 10 ms the panel replies `E0` + 22 data bytes:

| Byte | Meaning |
| --- | --- |
| 1–8 | 0 (unknown) |
| 9 | 127 (unknown) |
| 10 | 0 (unknown) |
| 11 | the `C2` value |
| 12–19 | faders 1–8 |
| 20 | MASTER |
| 21–22 | the `C1` value |

This is the only way to learn fader positions that haven't moved since start-up.

## LEDs

`88 01 08 b0 b1 b2 b3 b4 b5 b6 b7 F7` sets every panel LED. All zeros turns them all off. Mapped on hardware by lighting each bit alone:

| Bit | b0 | b1 | b2 | b3 | b4 | b5 | b6 | b7 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | track 1 red | track 1 green | track 2 red | track 2 green | track 3 red | track 3 green | track 4 red | track 4 green |
| 1 | track 5 red | track 5 green | track 6 red | track 6 green | track 7 red | track 7 green | track 8 red | track 8 green |
| 2 | MASTER red | MASTER green | PLAY | STOP | REC | ON/OFF 1 | ON/OFF 2 | none |

Bits 3–7 light nothing. Orange on a status key is its red and green bits together.

Three more LEDs are DSP GPIO outputs, active high: TEMPO = GP7[9], PEAK 1 = GP7[11], PEAK 2 = GP7[12].

## Commands with unknown effect

| Command | Notes |
| --- | --- |
| `89 01 08 <8 bytes> F7` | sent by the stock LCD task from a bitmap; all bits on or off changed nothing visible |
| `AC 01 02 xx yy F7`, `AD 01 02 xx yy F7` | unknown; stock sends `AC` alongside `A6` (pad lights) |

Only send commands the stock firmware sends. The panel processor is part of the recovery path, and we know no way to recover it. The few values we sent on purpose that stock may not (every bit set in `88` and in `89`) had no visible or lasting effect, which is no guarantee for others.
