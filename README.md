# Zoom R8 custom firmware

Notes, source code and tools for running your own code on the Zoom R8 multitrack recorder (stock firmware 1.17).

Start with the writeup: [writeup/README.md](writeup/README.md). To build the code and run it on your R8, follow [writeup/getting-started.md](writeup/getting-started.md).

## A Human Introduction

I created this repo because I have been doing Claude-assisted reverse-engineering of the Zoom R8, a recorder/sampler/interface/controller. My original goal was to add a feature to reverse a track, and I ended up going more deeply into the rabbit hole than I intended. This repo captures what I have learned so that other folks who are interested in using the R8 can take advantage of the work I did and build off of it. The documentation is meant to be consumable by a human, but it should also be useful as a source of information for further LLM-assisted work. There is also a Claude skill that helps with modifying existing firmware and creating new firmware.

The Zoom R8 is over a decade old, but the hardware is quite capable and it features a compact but comfortable control surface; I believe that it can serve as a great platform for developing new audio-based applications. I hope folks find it useful.

## Layout

| Directory | Contents |
| --- | --- |
| `writeup/` | Notes for firmware writers: hardware, stock firmware, safety, techniques, panel protocol, addresses |
| `cbuild/` | C and assembly source for the apps (Forth, Blippoo, PADTEST, POWERLOG, the chooser, utilities, the standalone synth) and the shared runtime |
| `tools/` | Python tools: firmware image handling, packaging, the C674x simulator and the test scripts, `fs.py` (talk to the Forth app), `r8card.py` (card status and deploy), `run_tests.py`, `doccheck.py` |
| `.claude/` | For Claude Code: the `r8-firmware` skill (how to work on this project safely) and a hook (`tools/r8guard.py`) that blocks commands breaking the safety rules |

## What you need to supply

This repository contains no Zoom code. The launcher build and several tests read your own copy of the stock firmware:

1. Create a directory `fw/` at the top of this repository (git ignores it).
2. Put your stock `R8SYS.BIN` (version 1.17) in it. Zoom publishes it with the R8 firmware update.

A few tests compare against captures from a real unit, which they read from `data/` (also ignored).

The build uses TI's C6000 code generation tools (`cl6x`). See [writeup/10-tools.md](writeup/10-tools.md).

## License

MIT: see [LICENSE](LICENSE). It covers the code, tools and writeup in this repository, including the apps' LCD font, which is our own design (`cbuild/forth/font.txt`). An optional build (`FONT=stock`) uses the stock font from your own firmware instead; binaries built that way contain Zoom's data and shouldn't be shared. It does not cover Zoom's firmware, which you supply yourself, or the launcher image a build makes from it (see [writeup/03-installing.md](writeup/03-installing.md)).
