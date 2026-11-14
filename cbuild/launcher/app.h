/* APP.BIN: a program for the app launcher (RUN APP in TOOL > SD CARD). 32-byte header, then the image.
   The launcher stages the image in idle DDR, checks it, takes the machine over from the stock firmware
   (interrupts off, EDMA events off, McASP0 reset, L2 all SRAM) and copies it to `load`, then jumps to `entry`.
   The app owns the machine until power-off; it gets no stack (set B15 first, as r8_boot does) and must
   zero its own uninitialised data (load + len .. top). */
#ifndef APP_H
#define APP_H
#define APP_MAGIC    0x50413852u        /* 'R8AP' */
#define APP_VERSION  1u
#define APP_LOAD_LO  0x11800000u        /* L2 RAM */
#define APP_TOP_MAX  0x1182C000u        /* image + uninitialised data; 16 KB below for the app's stack */
#define APP_STAGE    0xC0080000u        /* idle DDR (inside the updater's verify buffer) */
#define APP_STAGE_MAX 0x30000u
typedef struct { unsigned magic, version, load, len, entry, crc, top, rsvd; } app_hdr;
#endif
