/* App launcher, part of the flash-loaded code (linked at 0xC0072000, after the REVERSE code).
   r8_main runs as the RUN APP menu handler: on any problem it shows a popup and returns to the menu;
   otherwise it never returns. */
#include "../r8.h"
#include "app.h"
#include "util.h"
#define REG(a) (*(volatile u32 *)(a))
extern cregister volatile unsigned int IER, ICR, CSR;
extern void r8_launch(u32 src, u32 dst, u32 len, u32 entry);    /* jump.asm: no stack, never returns */

static u32 crc32(const u8 *p, u32 n)                    /* zlib CRC-32 */
{
    u32 c = 0xFFFFFFFFu;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1))); }
    return ~c;
}

static int load(app_hdr *h)
{
    int fd; u32 got;
    if (r8_open(&fd, "A:\\APP.BIN", 0, 0)) return 1;
    int bad = r8_read(fd, h, sizeof *h, &got) || got != sizeof *h
        || h->magic != APP_MAGIC || h->version != APP_VERSION
        || h->len == 0 || h->len > APP_STAGE_MAX
        || h->load < APP_LOAD_LO || h->load + h->len < h->load || h->top < h->load + h->len || h->top > APP_TOP_MAX
        || h->entry < h->load || h->entry >= h->load + h->len;
    for (u32 o = 0; !bad && o < h->len; o += got) {                 /* 32 KB reads, as REVERSE uses */
        u32 n = h->len - o < 0x8000u ? h->len - o : 0x8000u;
        bad = r8_read(fd, (void *)(APP_STAGE + o), n, &got) || got != n;
    }
    r8_close(fd);
    if (!bad && crc32((const u8 *)APP_STAGE, h->len) != h->crc) bad = 1;
    return bad ? 2 : 0;
}

static void takeover(void)
{
    CSR &= ~1u;                                         /* GIE off: the stock OS stops here */
    IER = 0; ICR = 0xFFF0u;
    static const u32 cc[] = { 0x01C00000u, 0x01E30000u };           /* EDMA3 CC0, CC1 */
    for (int i = 0; i < 2; i++) {
        u32 b = cc[i];
        REG(b + 0x1028) = 0xFFFFFFFFu;                  /* EECR: no more event-triggered transfers */
        REG(b + 0x1088) = 0xFFu;                        /* QEECR */
        REG(b + 0x1058) = 0xFFFFFFFFu;                  /* IECR */
        REG(b + 0x1040) = 0xFFFFFFFFu;                  /* SECR */
        REG(b + 0x1070) = 0xFFFFFFFFu;                  /* ICR */
        REG(b + 0x0308) = 0xFFFFFFFFu;                  /* EMCR */
    }
    for (volatile int i = 0; i < 20000; i++) ;          /* let transfers in flight finish (~0.1 ms) */
    REG(0x01D00044u) = 0;                               /* McASP0 GBLCTL: stop the stock audio stream */
}

/* RUN UTIL: load an R8UT file straight to its load address (idle DDR), check it, make the caches coherent and call
   it as a function; the stock firmware keeps running and we return to the menu. */
static int load_util(const char *path, util_hdr *h)
{
    int fd; u32 got;
    if (r8_open(&fd, path, 0, 0)) return 1;
    int bad = r8_read(fd, h, sizeof *h, &got) || got != sizeof *h
        || h->magic != UTIL_MAGIC || h->version != UTIL_VERSION || h->len == 0
        || h->load < UTIL_LO || h->load + h->len < h->load || h->top < h->load + h->len || h->top > UTIL_HI
        || h->entry < h->load || h->entry >= h->load + h->len;
    for (u32 o = 0; !bad && o < h->len; o += got) {
        u32 n = h->len - o < 0x8000u ? h->len - o : 0x8000u;
        bad = r8_read(fd, (void *)(h->load + o), n, &got) || got != n;
    }
    r8_close(fd);
    if (!bad && crc32((const u8 *)h->load, h->len) != h->crc) bad = 1;
    if (bad) return 2;
    r8_cache_wbinv((const void *)h->load, h->len, R8_CACHE_ALL, 1);
    r8_cache_inv_l1p();
    return 0;
}
typedef u32 (*util_fn)(const char *path);
static u32 run_util(const char *path)
{
    util_hdr h;
    int e = load_util(path, &h);
    if (e) { r8_popup(e == 1 ? R8_STR_NO_FILE : R8_STR_INVALID_FILE); return 0; }
    ((util_fn)h.entry)(path);
    return 0;
}

u32 r8_main(u32 a, u32 b)
{
    if (a == 1) return run_util(UTILMENU_PATH);           /* RUN UTIL */
    app_hdr h;
    int e = load(&h);
    if (e) { r8_popup(e == 1 ? R8_STR_NO_FILE : R8_STR_INVALID_FILE); return 0; }
    takeover();
    r8_launch(APP_STAGE, h.load, h.len, h.entry);
    return 0;
}
