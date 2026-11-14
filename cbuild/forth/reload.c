/* RELOAD: start A:\APP.BIN again without a power cycle. Reads and checks it as the RUN APP launcher does
   (tools/mkapp.py header: 'R8AP', version 1, load, len, entry, crc32(image), top, 0), then the caller stops audio and
   USB and reload_go() copies it over the running app from a trampoline in DDR (tramp.asm). */
#include "reload.h"
#include "fat.h"
#define STAGE      0xC0300000u           /* file is read here (below the looper's slots at 0xC0400000) */
#define TRAMP_AT   0xC03F0000u
#define LOAD_LO    0x11800000u
#define TOP_MAX    0x1182C000u
#define STAGE_MAX  0x30000u
#define L1DWBINV   0x01845044u
#define L1PINV     0x01845028u
extern u8 tramp_start[], tramp_end[];
typedef void (*tramp_fn)(const u32 *src, u32 *dst, u32 words, u32 entry);
static u32 app_load, app_len, app_entry;

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
    if (e.size < 36 || e.size > STAGE_MAX + 32) return "APP.BIN has a bad size";
    if (fat_read(&e, (void *)STAGE, e.size, poll)) return "read error";
    const u32 *h = (const u32 *)STAGE;
    if (h[0] != 0x50413852u || h[1] != 1) return "not an R8 app (header)";
    app_load = h[2]; app_len = h[3]; app_entry = h[4];
    if (app_len + 32 > e.size || app_len == 0 || (app_len & 3)) return "length does not match the file";
    if (app_load < LOAD_LO || app_load + app_len > TOP_MAX || h[6] > TOP_MAX) return "does not fit L2";
    if (app_entry < app_load || app_entry >= app_load + app_len) return "entry outside the image";
    if (crc32((const u8 *)STAGE + 32, app_len) != h[5]) return "CRC mismatch";
    return 0;
}
void reload_go(void)
{
    u32 n = (u32)(tramp_end - tramp_start);
    u8 *t = (u8 *)TRAMP_AT;
    for (u32 i = 0; i < n; i++) t[i] = tramp_start[i];
    REG(L1DWBINV) = 1; while (REG(L1DWBINV)) ;         /* the trampoline's bytes reach DDR */
    REG(L1PINV) = 1; while (REG(L1PINV)) ;              /* and no stale program lines remain */
    ((tramp_fn)TRAMP_AT)((const u32 *)(STAGE + 32), (u32 *)app_load, app_len / 4, app_entry);
    for (;;) ;
}
