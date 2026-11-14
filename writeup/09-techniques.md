# 9. Techniques

The methods that did most of the work, each with an example from the R8. None needs special hardware beyond the R8, a computer and an SD card. A USB audio interface and a phone camera helped with two of them.

## Work backwards from the manual

The operation manual describes what every control does. Search the firmware for the visible result of that action (a screen title, a message, a pin) and walk the code back to whatever triggers it. This finds things that watching the hardware does not.

**Example: the TEMPO key.** Logging the panel link showed every key except TEMPO. The manual says pressing TEMPO opens a tempo pop-up. The string "TEMPO" in the firmware led to a pop-up descriptor (title, handler, 3-second timeout), referenced from entry 0x28 of a key-dispatch jump table. Event 0x28 is posted from an input-change monitor that polls five DSP GPIO pins, one of them GP6[0]. Watching that pin from Forth showed it pulse on each press. TEMPO is wired to the DSP, not the panel processor.

**Example: the TEMPO LED.** The manual says the indicator flashes in time with the tempo. The firmware's beat timer toggles GP7[9], and setting that pin from Forth lit the LED. The PEAK LEDs on GP7[11] and GP7[12] were found next to it the same way.

**Example: the audio clock.** The firmware has a task named `AudioClkMng`. Its request queue led to two paths: one programs an I2C device at address 0x54, the other switches two GPIO pins. The choice depends on a board ID read from three strap pins. Reading those pins on our unit (ID 7) showed which path applies. Replaying the I2C writes from Forth switched the R8 between 44.1 and 48 kHz, confirmed by a test tone moving by exactly 48000/44100. Varying the values and measuring the tone then identified the chip as a fractional-N PLL.

## Capture traffic with a patched stock firmware

Before opening the case for a logic analyzer, check whether the firmware can log for you. The stock firmware's UART driver has a small number of call sites. Retargeting those calls to a hook that records each byte, with a timestamp, into a RAM buffer, then saving the buffer when a menu is opened, gave a complete two-way log of the panel link. A guided session (press this, move that, with timestamps) then let a script attribute each message type to an action.

The same idea works for any driver whose call sites you can find. Keep the hook transparent: same arguments in, same results out, no side effects on registers the caller relies on.

## Reproduce each hardware bug in the simulator first

Our C674x simulator runs the real firmware binary against Python models of the board (GPIO, SPI and LCD, the panel link, McASP with its FIFO, the SD card, USB, I2C, timers, interrupts). Every change runs there before it runs on the R8.

The rule that made this valuable: **when hardware disagrees with the simulator, change the model until the old firmware fails in the simulator the same way, then fix the firmware.** Examples:

- **The McASP transmitter stall:** the model now stalls after an underrun as the hardware does, and failed the old code the same way.
- **The SD card's 4 KB faults:** the card model inserts and drops the same bytes as the controller when a multi-block command lacks DMATRIG, so code that crosses 4 KB without it fails a test.
- **The I2C race:** the model keeps STOP pending for about 10 µs after the stop flag, and the old build failed exactly as on hardware. The first attempt at the model did not fail the old build. The reasons (it signalled bus-busy, and the window was scaled by the simulator's time compression) were themselves useful facts about the hardware.
- **The channel swap:** the model now has the McASP's one-slot transmit buffer and optional start skews, and the fix is tested with all four combinations.

The reverse also happens: tests can be wrong. Twice a test failed because its own measurement was wrong (a tone detector across a loop wrap, a buffer read at the wrong offset). Check the measurement before changing the code.

## When stock does it better, compare its driver with yours

From 8 to 14 November our SD driver split every transfer into 7-block runs to avoid a fault at 4 KB, which limited reads to 3.45 MB/s. Then a Mac reading from stock's CARD READER mode reached 9.3 MB/s, so stock clearly didn't have the fault. Tracing stock's driver gave a short list of differences (one command per request, DMA, the command bits, the clock). Two Forth words that tried each difference on its own, read-only first and then on unused blocks, found the one that mattered: one bit, DMATRIG, in the command register (section 6). The lesson: a limit that the stock firmware doesn't have is a bug in your code or your understanding, not in the hardware. List what stock does differently and test the items one at a time.

## Use instruments on the bench

- **An interactive prompt on the device.** A small Forth over USB serial lets you read and write any register or memory address, call driver functions, and define test words on the fly. Most register questions were answered this way in minutes, without a build.
- **A USB audio interface as ears.** Its outputs feed the R8's inputs and the R8's outputs feed it back. Scripts play test tones and measure frequency and level, so audio changes are verified by numbers rather than by listening. Measuring frequency to 0.01 Hz exposed the clock change (8.8%) and the 1% PLL step exactly.
- **A phone camera for LEDs.** The Mac's Continuity Camera took a photo of the panel after each LED bit was set, and a difference script found which LED had lit. Sixty-four bits were mapped without anyone watching.
- **Files on the card as a log.** Patched stock firmware can write diagnostic files with the firmware's own file API. The REVERSE feature logged every call it made and its result, which settled a "did it work?" question that observing the unit could not.

## Run inside the stock firmware to ask it questions

Code that runs inside the stock firmware (a utility, section 7) can call any stock function and read any stock structure while the R8 is otherwise normal. Two patterns did most of the work:

- **Dump the raw structure, then decide.** Before writing a file picker, a diagnostic utility called the stock find-first and find-next functions and saved every raw entry to the card. Reading the dump on the computer settled the entry layout in one run. When the first picker then missed most folders, a second diagnostic dumped the listings of the root and a subfolder and showed why: `*.*` only matches names with a dot.
- **Wrap a stock hook and listen.** A test utility wrapped the last per-block pointer in the stock audio interrupt, played a different tone on each output pair, and recorded the inputs. One run answered four questions: whether the interrupt keeps running under a menu, which words drive the line out and which the headphones, whether those are independent, and (with the line out cabled to the inputs) the round-trip latency, from the tone's onset and a phase fit.

When something looks wrong in the stock UI under your code, trace it in the stock code before working around it. The black bar on our menus turned out to be a stock bug (section 6) that Zoom's own menus never trigger.

## Log everything, then let the user press things

When you don't know what a control sends, don't decode it live. A small app that saves every byte from the panel link with a microsecond timestamp, plus a screen showing events as they arrive, let the user try pads, held pads and two pads at once at their own pace. The log answered the pad encoding, the lack of pressure, and turned up a key (`A7`) that the map called unused. A list of recent events on screen, labelled by the key map, then let the user find which control that was.

## Check the existing map before a new experiment

Before writing a test to find something, check whether earlier results already predict it. We once built a key logger to find the stock UI event codes for ENTER, EXIT and STOP; the key map from two days earlier already gave them as `0x10000 | ID`. A test is still worth running to confirm a prediction, but say beforehand what you expect.

## Wrap raw code so the vendor's disassembler reads it

TI's disassembler (`dis6x`, part of the C6000 compiler) decodes every C674x instruction, including the 16-bit compact forms that hand-written decoders get wrong. It only reads object files, not raw images. A minimal ELF file around a slice of the firmware works. It needs one executable section at the right address, plus a copy of the `.c6xabi.attributes` section from any object the compiler produced, which tells it the instruction set.

## Change one thing, and measure

- **Same-length edits first.** The first modified firmware changed one string to another of the same length and fixed the checksum. That proved the updater accepts modified files before anything risky was tried.
- **One question per build.** Each early hardware build answered a short list of questions written down beforehand (where does the bar appear, does it fill left to right, what erases it). Results were recorded against the list.
- **Predict, then measure.** For the clock chip, we predicted the setting for exactly +1% and the resulting tone (932.35 Hz) before trying it. Matching a prediction is much stronger evidence than explaining a result afterwards.
