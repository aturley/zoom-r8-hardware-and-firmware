/* Stock Zoom R8 v1.17 firmware functions, called from flash-loaded C code.
   Addresses come from r8_stock.cmd; all calls are far (built with --mem_model:code=far). */
#ifndef R8_H
#define R8_H
typedef unsigned int  u32;
typedef unsigned short u16;
typedef unsigned char u8;
typedef int s32;
typedef short s16;

/* string */
extern u32  r8_strlen(const char *s);

/* file API: all return 0 on success */
extern int  r8_open(int *fd, const char *path, int mode, int attr);   /* mode 0 read, 2 rw, 0x101 create (attr 0x80) */
extern int  r8_read(int fd, void *buf, u32 len, u32 *got);
extern int  r8_write(int fd, const void *buf, u32 len, u32 *n);
extern int  r8_seek(int fd, s32 off, int whence);                      /* 2 from start, 0 relative */
extern int  r8_close(int fd);
extern int  r8_delete(const char *path);
extern int  r8_rename(const char *from, const char *to);
extern int  r8_info(const char *path, void *info);                     /* 0 = exists; info[0x133] bit0 = read-only */

/* tracks */
extern const char *r8_track_file(int track);                           /* full path of the track's WAV */
extern void r8_track_detach(int track);
extern int  r8_track_attach(const char *path, int track);
extern void r8_clear_undo(void);
extern int  r8_card_check(int what);                                   /* TRIM uses 11 */

/* UI */
extern void r8_pre_msg(void);
extern void r8_show_msg(const char *s);                                /* "Please Wait..." style dialog */
extern void r8_dialog_close(int ok);
extern void r8_popup(const char *s);                                   /* error popup */

/* cache (SYS/BIOS): block write-back + invalidate of L1D/L2, then L1P invalidate, before running loaded code */
extern void r8_cache_wbinv(const void *addr, u32 len, int type, int wait);   /* SYS/BIOS Cache_wbInv */
#define R8_CACHE_ALL 0x7FFF
extern void r8_cache_inv_l1p(void);

/* folder listing, as stock uses it for \\*.WAV (0xC0771288): find_first(pattern, attr mask 0x17, find) returns 0
   with the first match in *find; find_next(find) the next; -10406 = no more; find_close(find) when done.
   The find structure layout is confirmed on hardware (see utils/browse.h: 8.3 name at +0x12, long name at +0x1F). */
#define R8_FIND_SIZE 0x160
#define R8_FIND_END  (-10406)
extern int  r8_find_first(const char *pattern, int attr, void *find);
extern int  r8_find_next(void *find);
extern int  r8_find_close(void *find);

/* menus: r8_menu_engine(descriptor, 0, 0, 0) opens a menu (as the TOOL > SD CARD item does) */
extern void r8_menu_engine(void *desc, int b, int c, int d);
/* the UI's next event (what the menu engine reads, with -1): waits on the UI mailbox (B14[25]) for up to timeout
   ticks (negative = forever), then translates the panel message into an event code (0x10018 ENTER, 0x10014/15
   cursor, 0x10000000 wheel; see the work log) */
extern int  r8_ui_event(int timeout);
#define R8_EV_ENTER 0x10018
#define R8_EV_EXIT  0x10013
#define R8_EV_STOP  0x10020
#define R8_EV_WHEEL 0x10000000
/* the current sample rate (B14[818]; the UI compares it with 44100 and 48000) */
extern u32  r8_sample_rate(void);

/* firmware strings */
#define R8_STR_PLEASE_WAIT   ((const char *)0xc06e77dd)
#define R8_STR_NO_FILE       ((const char *)0xc06e77c5)
#define R8_STR_PROTECTED     ((const char *)0xc06e77ce)
#define R8_STR_ACCESS_ERROR  ((const char *)0xc06e7960)
#define R8_STR_INVALID_FILE  ((const char *)0xc06e785d)

/* globals */
#define R8_TRACK_INDEX   (*(volatile int *)0xc0813024)
#define R8_STEREO_MASK   (*(volatile u32 *)0xc05b3240)
#endif
