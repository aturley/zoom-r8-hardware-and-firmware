# Custom firmware for the Zoom R8: notes for firmware writers

These notes describe the Zoom R8 multitrack recorder from the point of view of someone who wants to run their own code on it. They cover how the hardware and the stock firmware work, what is safe to try, the tools and techniques that got us this far, and the problems we ran into.

Everything here was worked out on one R8 running stock firmware SYSTEM 1.17 (SUB SYS 1.0, BOOT 1.0), between 6 and 14 November 2026. Where a fact was confirmed on the hardware, the text says so. Where it was only read from the stock firmware, or inferred, it says that instead.

**If you only want to install someone's alternative firmware**, read [Safety and recovery](02-safety-and-recovery.md) and [Installing an alternative firmware](03-installing.md). Alternative firmwares are best distributed as standalone projects with source and binaries. These notes are about writing one.

**To build and run the code in this repository**, read [Safety and recovery](02-safety-and-recovery.md), then [Getting started](getting-started.md): from a fresh clone to the Forth app on your R8, and the loop for trying new code.

## Contents

| Section | What it covers | Status |
| --- | --- | --- |
| [Getting started](getting-started.md) | Build everything, install the launcher, run the Forth app, the development loop | new (14 Nov) |
| [Using the apps and utilities](apps-guide.md) | The chooser, FORTH, SAMPLER, BLIPPOO, PADTEST, POWERLOG and the RUN UTIL utilities, from the panel | new (14 Nov) |
| [1. The R8](01-the-r8.md) | The product, the manual, the versions we worked on | edited (14 Nov) |
| [2. Safety and recovery](02-safety-and-recovery.md) | What can and cannot brick the unit, and how to get back to stock | edited (14 Nov) |
| [3. Installing an alternative firmware](03-installing.md) | The update procedure, for users | edited (14 Nov) |
| [4. Stock firmware](04-stock-firmware.md) | File format, flash layout, update and boot sequence, inside 1.17 | edited (14 Nov) |
| [5. Hardware](05-hardware.md) | Each subsystem: how it works, registers and pins, unknowns; the stock audio interrupt | edited (14 Nov) |
| [6. Hardware quirks](06-quirks.md) | Behaviour that is not in TI's documentation, and stock-firmware gotchas, found the hard way | edited (14 Nov) |
| [7. Building custom firmware](07-building-custom-firmware.md) | Patching stock, utilities inside stock, standalone images, apps loaded from the card | edited (14 Nov) |
| [8. The Forth app](08-forth-app.md) | The reference app: architecture, words, panel controls | edited (14 Nov) |
| [9. Techniques](09-techniques.md) | Methods that worked, with worked examples | edited (14 Nov) |
| [10. Tools](10-tools.md) | What we used and what we built | edited (14 Nov) |
| [11. Timeline](11-timeline.md) | The steps and decisions, briefly | edited (14 Nov) |
| [12. Open questions](12-open-questions.md) | What is still unknown | edited (14 Nov) |
| [Appendix A: panel protocol](appendix-a-panel-protocol.md) | Keys, faders, wheel, pads, footswitch, LEDs | edited (14 Nov) |
| [Appendix B: addresses and registers](appendix-b-addresses.md) | Stock functions, data and DSP registers we use (stock 1.17 only) | edited (14 Nov) |

## Conventions

- Addresses such as `0xC07F3324` are in stock firmware 1.17 unless stated otherwise. They will differ in other versions.
- Register names follow TI's documentation for the C6748 / OMAP-L138 family.
- "Confirmed on hardware" means we saw it on the R8. "From firmware" means we read it from the stock code. "Inferred" means it is our best reading but untested.

## Publishing

This project will be published on GitHub under the MIT license (see `LICENSE` at the top of the repository).

### What is and isn't included

These notes describe behaviour, addresses, register values and our own code. They do not include Zoom's firmware, and should not. If you publish work based on them, keep it that way: distribute your own code and patch tools, not Zoom's binaries or large excerpts of their disassembly.
