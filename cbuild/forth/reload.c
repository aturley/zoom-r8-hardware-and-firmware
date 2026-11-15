/* Loading an app file (tools/mkapp.py): used by the chooser to start an app and by RELOAD to restart one.
   reload_check() reads the file into the stage and checks it; reload_go() copies it into place from a trampoline in
   DDR (tramp.asm) and jumps in. Header: 'R8AP', version, load, len, entry, crc32, top, nseg.
     version 1: the L2 image only (the flash launcher's format too); crc32 over the image; nseg = 0.
     version 2: the L2 image, then nseg DDR segments {addr, len, data}, all inside the app DDR window
                0xC0000000-0xC00FFFFF; crc32 over everything after the header.
   DDR map while an app runs (writeup section 7): 0xC0000000-0xC00FFFFF app DDR window; 0xC0100000-0xC023FFFF core
   buffers (USB storage, SD tests); 0xC0300000-0xC037FFFF this stage; 0xC0380000 fat.c's cluster list (32 KB);
   0xC03F0000 the trampoline, 0xC03F8000 its copy list; 0xC03FFF00 launch info; 0xC0400000 up: the app's buffers. */
#include "reload.h"
#include "fat.h"
#define STAGE      0xC0300000u           /* file is read here (below the looper's slots at 0xC0400000) */
#define TRAMP_AT   0xC03F0000u
#define LOAD_LO    0x11800000u
#define TOP_MAX    0x1182C000u
#define FILE_MAX   0x80000u              /* the stage, up to fat.c's cluster list at 0xC0380000 */
#define LIST_AT    0xC03F8000u           /* the trampoline's copy list */
#define DDR_LO     0xC0000000u           /* the app DDR window (cbuild/forth/l2.cmd) */
#define DDR_HI     0xC0100000u
#define MAX_SEGS   8
#define L1DWBINV   0x01845044u
#define L1PINV     0x01845028u
extern u8 tramp_start[], tramp_end[];
typedef void (*tramp_fn)(const u32 *list, u32 entry);   /* list: {src, dst, words} triples, words = 0 ends it */
static u32 app_load, app_len, app_entry, app_nseg;
static const u32 *seg_hdr[MAX_SEGS];                    /* each DDR segment's {addr, len} in the stage, data after */

static u32 crc32(const u8 *p, u32 n)
{
    u32 c = ~0u;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = c >> 1 ^ (0xEDB88320u & -(c & 1)); }
    return ~c;
}
void reload_set_launched(const char *path)
{
    volatile u32 *w = (volatile u32 *)LAUNCH_INFO; char *p = (char *)(LAUNCH_INFO + 4);
    int i = 0; for (; path[i] && i < 59; i++) p[i] = path[i];
    p[i] = 0; w[0] = LAUNCH_MAGIC;
}
const char *reload_launched(void)
{
    const char *p = (const char *)(LAUNCH_INFO + 4);
    if (*(volatile u32 *)LAUNCH_INFO != LAUNCH_MAGIC) return 0;
    for (int i = 0; i < 60; i++) if (!p[i]) return i ? p : 0;
    return 0;
}
const char *reload_check(const char *path, void (*poll)(void))
{
    fat_ent e;
    if (fat_mount()) return "no FAT32 card";
    if (fat_find(path, &e) || (e.attr & 0x10)) return "file not found";
    if (e.size < 36 || e.size > FILE_MAX) return "APP.BIN has a bad size";
    if (fat_read(&e, (void *)STAGE, e.size, poll)) return "read error";
    const u32 *h = (const u32 *)STAGE;
    if (h[0] != 0x50413852u || (h[1] != 1 && h[1] != 2)) return "not an R8 app (header)";
    app_load = h[2]; app_len = h[3]; app_entry = h[4]; app_nseg = h[1] == 2 ? h[7] : 0;
    if (app_len + 32 > e.size || app_len == 0 || (app_len & 3)) return "length does not match the file";
    if (app_nseg > MAX_SEGS || (h[1] == 2 && !app_nseg)) return "bad segment count";
    u32 off = 32 + app_len;
    for (u32 k = 0; k < app_nseg; k++) {                      /* DDR segments: inside the window, inside the file */
        if (off + 8 > e.size) return "length does not match the file";
        const u32 *sg = (const u32 *)(STAGE + off);
        u32 a = sg[0], n = sg[1];
        if (!n || (a & 3) || (n & 3) || a < DDR_LO || a > DDR_HI || n > DDR_HI - a) return "DDR segment outside the window";
        if (n > e.size - off - 8) return "length does not match the file";
        seg_hdr[k] = sg; off += 8 + n;
    }
    if (h[1] == 2 && off != e.size) return "length does not match the file";
    if (app_load < LOAD_LO || app_load + app_len > TOP_MAX || h[6] > TOP_MAX) return "does not fit L2";
    if (app_entry < app_load || app_entry >= app_load + app_len) return "entry outside the image";
    if (crc32((const u8 *)STAGE + 32, h[1] == 2 ? e.size - 32 : app_len) != h[5]) return "CRC mismatch";
    return 0;
}
void reload_go(void)
{
    u32 n = (u32)(tramp_end - tramp_start);
    u8 *t = (u8 *)TRAMP_AT;
    for (u32 i = 0; i < n; i++) t[i] = tramp_start[i];
    REG(L1DWBINV) = 1; while (REG(L1DWBINV)) ;         /* the trampoline's bytes reach DDR */
    REG(L1PINV) = 1; while (REG(L1PINV)) ;              /* and no stale program lines remain */
    u32 *l = (u32 *)LIST_AT;                             /* what to copy where: the L2 image, then each DDR segment */
    *l++ = STAGE + 32; *l++ = app_load; *l++ = app_len / 4;
    for (u32 k = 0; k < app_nseg; k++) { *l++ = (u32)(seg_hdr[k] + 2); *l++ = seg_hdr[k][0]; *l++ = seg_hdr[k][1] / 4; }
    *l++ = 0; *l++ = 0; *l = 0;
    ((tramp_fn)TRAMP_AT)((const u32 *)LIST_AT, app_entry);
    for (;;) ;
}
