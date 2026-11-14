# 3. Installing an alternative firmware

This section is for people who want to run someone else's firmware, not write their own. Read [Safety and recovery](02-safety-and-recovery.md) first.

An alternative firmware for the R8 can come in three forms:

- **A replacement main firmware**, an `R8SYS.BIN` file, installed with the R8's normal firmware update procedure. It replaces Zoom's firmware until you reinstall Zoom's.
- **An app**, a `.BIN` file on the SD card, started from a menu item (RUN APP) that a modified main firmware adds. This needs the modified main firmware installed first. In this project RUN APP starts `A:\APP.BIN`, which is an app chooser, and the apps themselves go in `A:\R8APPS`.
- **A utility**, a `.BIN` file in `A:\R8UTILS`, started from another added item (RUN UTIL). It runs inside Zoom's firmware and returns to its menu. This also needs the modified main firmware.

Whatever the form, the author should publish source and binaries as a standalone project, with the exact stock version it was built from.

**A modified main firmware can't be downloaded ready-made.** It is Zoom's firmware with changes, so it can't be redistributed. Its author should publish a patch tool instead, which you run on your own copy of Zoom's `R8SYS.BIN` for the right version. This project's launcher works that way: [Getting started](getting-started.md) builds it from your own 1.17 file. Apps and utilities contain no Zoom code and can be shared as files. (Until 14 November our apps' LCD font was copied out of the stock firmware at build time; they now use a font of our own, `cbuild/forth/font.txt`. Building with `FONT=stock` still copies the stock font from your own firmware, for your own use; binaries built that way shouldn't be shared.)

## Installing a replacement R8SYS.BIN

The procedure is the R8's own firmware update (operation manual p. 114, "Upgrading the firmware"). In outline:

1. Check your version (TOOL → SYSTEM → VERSION) and keep a copy of Zoom's own R8SYS.BIN for it, so you can go back.
2. Copy the new `R8SYS.BIN` to the root of the SD card.
3. With the R8 off, insert the card and connect the AC adapter, as the manual asks for any update. Then hold **PLAY** and turn the power on.
4. Confirm: cancel is selected at first, so press LEFT to select OK, then ENTER. Do not turn the power off while it writes. It shows a completion message when done.
5. Turn the R8 off and on again normally.

## Going back to Zoom's firmware

Repeat the same procedure with Zoom's original `R8SYS.BIN`.

This works even if the alternative firmware crashes or shows nothing. Holding PLAY at power-on is handled by the R8's boot loader before the main firmware runs (section 2). We used it many times on our unit.

## What never to install

- Any `R8BOOT.BIN` (the boot loader) or `R8SUB.BIN` (the panel processor's firmware) that did not come from Zoom. Nothing we know of recovers from a bad one.
- A main firmware built for a different version of the stock firmware than the one it says it patches.
