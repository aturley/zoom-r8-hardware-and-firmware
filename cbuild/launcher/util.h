/* R8UT: a utility for RUN UTIL (TOOL > SD CARD). Unlike an app it runs INSIDE the stock firmware: it is loaded into
   DDR the firmware leaves idle (the boot loader's buffer area), called as a function from the menu task, and
   returns to the menu. It uses the firmware's own file API and dialogs (cbuild/r8.h) and must not take over any
   hardware. RUN UTIL starts A:\R8UTILS\UTILMENU.SYS, which lists the utilities (*.BIN) in A:\R8UTILS and runs
   the one picked with the same loader. Same 32-byte header as APP.BIN, magic 'R8UT'.
   Entry: u32 entry(const char *own_path), called on the menu task's stack (keep stack use small). */
#ifndef UTIL_H
#define UTIL_H
#define UTIL_MAGIC   0x54553852u        /* 'R8UT' */
#define UTIL_VERSION 1u
#define UTIL_LO      0xC0100000u        /* idle DDR while the stock firmware runs (0xC0050000..0xC0250000) */
#define UTIL_HI      0xC0240000u
#define UTILMENU_AT  0xC0100000u        /* UTILMENU.SYS links here (64 KB) */
#define UTILS_AT     0xC0110000u        /* utilities link here (up to UTIL_HI) */
#define UTILMENU_PATH "A:\\R8UTILS\\UTILMENU.SYS"
typedef struct { unsigned magic, version, load, len, entry, crc, top, rsvd; } util_hdr;
#endif
