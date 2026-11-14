# 6. Hardware quirks and gotchas

Each of these cost us time. Most are not in TI's documentation for the C6748, or are easy to miss in it. All were seen on our R8 unless marked otherwise.

## Audio (McASP0)

**The transmitter stalls after an underrun.** If the McASP misses a transmit slot, the next write to XBUF stalls the transmitter: XRDY stays 0, XSTAT.XDATA never sets again, and no further underruns are flagged. The receiver keeps working, so input looks fine while output is dead. Recovery: clear XSRCLR, XSMRST and XFRST in XGBLCTL while leaving XHCLKRST and XCLKRST set (the receiver uses the transmit clocks), clear XSTAT, prime the buffer, and set the bits again. Any audio engine must check for underruns and restart the transmitter, or output goes silent for good.

**With the FIFO, XEVTCTL and REVTCTL must be 0.** When feeding the McASP through its AFIFO (the data port at `0x01D02000`), the McASP's own DMA request is what moves words between the FIFO and the serializers. Setting XEVTCTL.XDATDMA = 1 disables that request: every slot underruns while the FIFO sits full. The same applies to REVTCTL.RDATDMA on the receive side.

**Left and right are not fixed at start-up.** Counting words from the moment you start the transmitter or receiver does not tell you which channel the next word belongs to. On our unit, both sides started one slot off in every session. Monitoring (input to output) and playback of recordings looked correct, because the two swaps cancelled. Stereo files played swapped, and recordings saved to the card were swapped. The fix is to ask the McASP: XSTAT bit 3 (XTDMSLT) and RSTAT bit 3 (RTDMSLT) are 1 while the slot on the wire is even (left, in I2S). Combined with the FIFO fill level, that gives the channel of the next word:

- Transmit: the next 2-word group written to the FIFO goes out in slot s + 2 + W/2 (s = slot on the wire, XBUF already holds s + 1, W = words queued). If that is a right slot, write one silent slot first.
- Receive: the oldest word in the read FIFO is from slot c − R (c = slot being received, R = words queued). If that is a right slot, drop one word.

Read the status register before and after the FIFO level, and retry if a slot boundary passed in between. Do this at start-up and after every restart.

**The headphones are a separate output.** Serializer 2 (headphones) and serializer 4 (line out) carry independent data. Code that writes only serializer 4 leaves the headphones silent, and code that copies one to the other loses the chance to send them different signals (section 5).

**XBUF holds one slot.** Without the FIFO, you have about 11 µs at 44.1 kHz (two slots per frame) to refill XBUF. Any long loop, such as drawing on the LCD, must keep feeding it. The FIFO (64 words, 16 stereo frames for two serializers) is much easier to live with.

## Audio clock

**The master clock hardware depends on the board revision.** The stock firmware reads three strap pins at boot (GP0[7], GP1[5], GP1[4]) into a board ID. Our unit reads 7. On board 7, the audio master clock comes from a fractional-N PLL on I2C0 at address 0x54. On other boards it comes from one of two oscillators, enabled by GP1[15] (44.1 kHz family) or GP0[9] (48 kHz family). Code that sets the sample rate must check the ID. Details in [Hardware](05-hardware.md).

**I2C: wait for STOP to finish, not just the stop flag.** After an I2C0 write, ICSTR.SCD (stop detected) sets before ICMDR.STP clears. Starting the next transfer in between loses its START: the controller drops out of master mode, ICMDR reads `0x6220` (STT still set, MST clear), and the next byte times out. Wait for ICMDR.STP to clear after every transfer, as the stock driver does. ICSTR.BB (bus busy) is already clear by then, so waiting for BB is not enough. If a transfer times out, reset the controller (ICMDR = 0, then the idle value `0x4220`).

## SD card (MMCSD1)

**Multi-block transfers of 4 KB or more fail unless MMCCMD's DMATRIG bit is set** (hardware, 2026-11-14). Without DMATRIG (bit 16) in the data command, a multi-block write gets a 0x00 inserted at byte 4095, with everything after it shifted by one, and a multi-block read loses byte 4094, so it ends one byte short and never completes (a timeout, with MMCST0 showing only RSPDNE; the card recovers). The fault starts at exactly 4096 bytes: 7 blocks are exact, 8 are not. It doesn't depend on the buffer address, the FIFO threshold, the card clock, DCLR or PPLEN, or whether the CPU or EDMA moves the data. With DMATRIG set, as stock sets it on every command, 8, 9, 64 and 128 blocks are exact in both directions, at several places on the card, with either the CPU or EDMA draining the FIFO.

| Data-command flags | 8 blocks (exactly 4 KB) and more |
| --- | --- |
| DCLR + PPLEN (our old flags), PPLEN only, none | reads never complete; writes get a zero at byte 4095 |
| **DMATRIG (bit 16) only, as stock** | **correct** for 8, 9, 64 and 128 blocks, reading and writing |

Our driver (`cbuild/forth/sd.c`) sets DMATRIG on multi-block data commands and uses runs of up to 128 blocks, at a 22.5 MHz card clock. Measured on the R8 through the driver: reads 10.3 MB/s, writes 5.3–5.7 MB/s. Before the fix we split every transfer into runs of 7 blocks, which read at 3.45 MB/s.

*How we found it.* Stock in CARD READER mode let a Mac read files at 9.3–9.4 MB/s (hardware, 2026-11-13), far faster than our 7-block runs, so stock was clearly not splitting transfers. Its driver (firmware, 1.17) differs from our old one in five ways:

- **Whole requests as one command.** A read of n blocks is one CMD17 (n = 1) or one CMD18 with MMCNBLK = n, followed by CMD12 (`0xC07A0428`; writes use CMD24/CMD25 in `0xC07A0268`). A chunking wrapper exists (`0xC07A04F8`, chunk size at `0xC06000A0`), but nothing sets the chunk size, so it is never used. (An earlier version of these notes said stock uses only single-block commands; that was wrong.)
- **DMA for the bulk of each transfer.** The data phase (`0xC07A7E88`) moves an unaligned head and tail with the CPU and the rest through a DMA driver reached by a function table (`B14[889]`): descriptors from `0xC07A834C` with source MMCDRR (+0x28) or destination MMCDXR (+0x2C), 4-byte elements, 16 per event (64 bytes). Which DMA engine the table drives was not traced (CC1 events 28/29 would fit).
- **Different command bits.** Stock's command words come from a table at `0xC0706F60`; CMD18 is `0x2212` (WDATX, R1 response) with no DCLR and no PPLEN, and stock ORs in DMATRIG on every command. Our old driver set DCLR and PPLEN and never DMATRIG.
- **A faster card clock.** Stock switches capable cards to SD high-speed mode with CMD6 (`0xC079FACC`) and then writes MMCCLK = `0x200`; otherwise it uses the card's TRAN_SPEED (0x32 → CLKRT 3, 22.5 MHz). What `0x200` gives depends on bit 9, which we read as a divide-by-4 (45 MHz; inferred, not checked). Ours ran at 18 MHz and now uses CLKRT 3; we haven't tried the high-speed switch.
- **Same FIFO setup.** FIFORST, then a 64-byte threshold and 4-byte access, as ours.

Two Forth words then tested the flags one at a time, in the unpartitioned gap before the first partition for writes: `SD-RCMP` reads blocks one at a time and again with one CMD18 and compares; `SD-WCMP` writes with one CMD25 and reads back block by block (section 8). Only DMATRIG mattered. One CMD18 of 128 blocks read 8.3–8.6 MB/s at 18 MHz and 10.4–10.6 MB/s at 22.5 MHz; one CMD25 of 128 blocks wrote 5.4–6.4 MB/s at 18 MHz and up to 7.5 MB/s at 22.5 MHz, including the card's programming time, which varies.

**MMCCMD.WDATX is bit 13.** Some register summaries place the data-transfer bit at bit 11 (that is DTRW, the direction). Linux's davinci_mmc driver agrees with bit 13.

**The FIFO threshold bit means 64 bytes, not 32.** MMCFIFOCTL bit 2 set gives a 64-byte threshold on this controller (clear gives 32). A DMA transfer that moves only half the threshold per event stalls after one event.

**DRRDY fires once per threshold crossing.** MMCST0 bits clear when read, and DRRDY is a one-shot. A CPU read loop should drain the FIFO while MMCST1.FIFOEMP is clear, not wait for DRRDY again.

**DCLR in the command empties the FIFO.** If you set it, fill the FIFO for a write only after writing MMCCMD, or the pre-fill is lost. (Stock doesn't set DCLR, and neither does our driver now.)

**A failed data transfer leaves the card mid-transfer.** After a timeout or error, send CMD12 (stop) and wait with CMD13 until the card is back in the transfer state. Otherwise every following command fails.

## EDMA3

**MMCSD1 is on the second channel controller.** MMCSD1 receive raises CC1 (base `0x01E30000`) event 28, confirmed by watching CC1's event register during an ordinary read. Transmit is presumably event 29. CC1 has no channel-to-PaRAM map, so channel n uses PaRAM set n.

**Cache.** DDR is cacheable in L1D. Write back and invalidate L1D around DMA into DDR buffers (we use the global L1DWBINV at `0x01845044`).

## USB0

**VBUS is never seen.** The controller reads DEVCTL = 0x80 with the cable connected, so session detection never starts. Run it in forced device mode (CFGCHIP2 = `0x4932`, then set DEVCTL.SESSION).

**The compiler can merge volatile register writes.** TI's cl6x can place two volatile stores in one execute packet. The MUSB INDEX register must be written before the endpoint CSR it selects, so put a `NOP` barrier between them.

## Panel and GPIO

**TEMPO is not a panel key.** All 38 other keys, and the REPEAT/STOP pad, arrive over the panel processor's serial link. TEMPO is wired to the DSP's GPIO GP6[0], active low, so monitoring the panel link never shows it. The TEMPO LED and the two PEAK LEDs are also DSP GPIO outputs (GP7[9], GP7[11], GP7[12], active high), not panel LEDs.

**The pads have no pressure.** They send a strike velocity and a release, nothing while held. Pressing a held pad harder sends nothing; easing off sends a release and pressing again a new soft hit. Don't design an instrument around aftertouch.

**REPEAT/STOP is not a pad.** The pad labelled REPEAT/STOP, below the MASTER fader, is wired as an ordinary key (`A7`), with no velocity. We thought its key ID unused until a byte log showed it: the 2026-11-08 key mapping never pressed it, and its entry in the stock key-dispatch table is only the shared default. Stock handles it in its pad code instead: rolls on the rhythm pads and looped playback on the loop-track pads (firmware, appendix A).

**LED messages are 12 bytes.** `88 01 08 b0 … b7 F7`. We once sent a 7-LED-byte version by mistake; check the length.

**Don't send panel commands the stock firmware doesn't send.** An unknown command could put the panel processor into a state we cannot undo, and it is part of the recovery path. The few we tried on purpose (appendix A, section 12) had no visible or lasting effect, which is no guarantee for the next one.

## Inside the stock firmware

These matter to code that runs inside Zoom's firmware (section 7, ways 1 and 2).

**The menu value scroller leaves a black bar.** When a row's value is longer than 10 characters and the row is selected, the menu draws it through a scroller and sets a "scrolling" flag (`0xC0813790`). Moving the cursor doesn't stop the scroller. If the newly selected row also has a long value, the row drawer sees the flag still set and only resumes the scroller, at the old row, so the new row shows as a solid inverted bar. Stock menus rarely have values that long, so it doesn't show in Zoom's own UI. Our fix: the value function learns from its return address whether it is drawing the selected row (`0xC078E7C4`) or another (`0xC078E980`); on the selected row, if the scroller was started for a different item, it stops it (`0xC07EC184`) and clears the flag, and the drawer starts it afresh. After any of our menus closes, the scroller is stopped too. Confirmed on hardware.

**The menu screen always has a divider at x = 60.** Long labels run into it and erase it. Put short text in the label column and long text in the value column (section 7).

**The wheel edits any row that isn't read-only.** A row whose record +28 holds a string pointer, not an integer, gets its pointed-to word changed by a wheel turn. Set record +24 bit 3 on such rows.

**`*.*` matches the DOS way.** The stock find function only matches `*.*` against names that contain a dot, so most folders vanish from the listing. Use `dir\*`. Confirmed on hardware.

**Get B14 offsets from the data page pointer, not by eye.** Our first audio test read the half flag at B14[213] instead of B14[821] (`0xC0812B20 + 4 × 821 = 0xC08137F4`). It always used the same half, and the test still produced sound, so the mistake showed only as a level 4 dB too high and a recording that advanced 4 frames per block. The simulator test had copied the same wrong constant, so it passed. It now derives the address from B14.

## Memory and boot

**DDR is not zeroed at power-on.** The boot loader uses DDR from about `0xC0050000` to `0xC0250000` as a buffer, so that area holds old data when your code starts. A patched firmware once kept a "done" flag there, read garbage as "already done", and silently skipped its work.

**Keep the boot loader's pin multiplexing for the panel UART.** PINMUX4 depends on the board revision; leave it as R8BOOT set it.

## Our development loop

**The simulator shares your assumptions.** A register bit taken from the wrong summary (MMCCMD.WDATX, above) or a wrong address (the half flag, above) was wrong in both the code and the board model, so the simulator passed. Check new register definitions against an independent source, such as Linux drivers or TI's reference manual, before trusting a passing test.

**Instruction cache.** When copying new code over running code (our RELOAD), write back L1D and invalidate L1P before jumping, or the old code keeps running from cache. Our simulator only discards decoded instructions on an L1P invalidate, so a missing invalidate fails there too.
