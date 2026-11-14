# 1. The R8

The Zoom R8 is a portable 8-track recorder that also works as a USB audio interface, a DAW control surface and a pad sampler with a rhythm machine. It records to an SD card at 44.1 or 48 kHz (two tracks at a time, eight in total) and runs on batteries, a mains adapter or USB power.

For a firmware writer, the interesting parts are a TI C674x floating-point DSP, 64 MB of DDR2, an SD card slot, a USB device port, two analog inputs, a stereo line output and a separate headphone output, a 128×64 monochrome LCD, and a front panel with 9 faders, keys, eight velocity-sensitive pads, a REPEAT/STOP pad that works as a key, a wheel and a footswitch jack (38 keys and the REPEAT/STOP pad on the panel link, and TEMPO on a pin of its own; appendix A). A separate processor runs the panel. Section 5 covers each part.

## Documents and links

- Zoom R8 Operation Manual (English, PDF): https://zoomcorp.com/documents/148/E_R8.pdf
- The separate Audio Interface Manual (shipped on the R8's SD card; mirrored at ManualsLib): https://www.manualslib.com/products/Zoom-R8-Interface-2896515.html
- Sound on Sound review (Tom Flint, April 2012): https://www.soundonsound.com/reviews/zoom-r8

The operation manual is more useful than it sounds. Its panel layout pages give the name of every control, and its descriptions of what each control does were the starting point for tracing several features into the firmware (section 9).

## The unit we worked on

| Item | Value |
| --- | --- |
| SYSTEM version | 1.17 |
| SUB SYS version (panel processor) | 1.0 |
| BOOT version | 1.0 |
| Board ID (from the strap pins the firmware reads) | 7 |
| SD card | 32 GB SDHC, FAT32 |

Other units may differ. Board ID 7 in particular selects how the audio clock is generated (section 5), and the stock firmware supports at least one other arrangement.
