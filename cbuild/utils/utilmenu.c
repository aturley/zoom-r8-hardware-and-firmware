/* UTILMENU.SYS: started by RUN UTIL (TOOL > SD CARD). Lists the utilities (*.BIN, not folders) in A:\R8UTILS by file
   name, sorted, as a stock menu titled R8 UTILS, and runs the one picked: it is loaded at its own address
   (0xC0110000 up, never over this menu at 0xC0100000), checked (header, range, CRC), the caches are made coherent,
   and it is called with its path. When it returns the menu is still there; EXIT returns to SD CARD.

   The menu is built the way the firmware's own SD CARD menu is (descriptor 0xC06DFFA8, item records like FORMAT's at
   0xC06DFF08), with our own state words and SD CARD's event callback, and opened with menu_engine(desc, 0, 0, 0) as
   TOOL opens SD CARD. Find structure (seen on the R8): +0x12 name, +0x11F attributes (0x10 = folder), +0x120 size. */
#include "r8.h"
#include "util.h"

#define MAXU   16
#define NAMEN  13                                  /* 8.3 + NUL */
#define F_NAME 0x12
#define F_ATTR 0x11F
#define SD_CARD_CALLBACK 0xC07ADF74u               /* SD CARD's event callback: filters some keys, passes the rest on */
#define REC_PAD_STR      0xC06E995Du               /* what stock records hold at +48..+60 */

static u8   find[0x200];                           /* static: the menu task's stack is small */
static char names[MAXU][NAMEN];
static char labels[MAXU][NAMEN];                   /* the names without .BIN: labels longer than 8 run over the line at x = 60 */
static char paths[MAXU][12 + NAMEN];
static int  nu;
static u32  recs[MAXU][20];
static u32  list[MAXU + 1];
static u32  state[2];
static u32  desc[12];
static const char title[] = "R8 UTILS";

static int is_util(const u8 *f)
{
    if (f[F_ATTR] & 0x10) return 0;                /* folders, '.', '..' */
    const char *n = (const char *)f + F_NAME;
    int k = 0; while (k < NAMEN - 1 && n[k]) k++;
    if (k < 5 || n[k] || n[k - 4] != '.') return 0;
    const char *e = n + k - 3;
    return (e[0] | 0x20) == 'b' && (e[1] | 0x20) == 'i' && (e[2] | 0x20) == 'n';
}
static int ncmp(const char *a, const char *b)
{
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}
static void scan(void)
{
    nu = 0;
    int r = r8_find_first("A:\\R8UTILS\\*.*", 0x17, find);
    while (r == 0) {
        if (is_util(find) && nu < MAXU) {
            const char *n = (const char *)find + F_NAME;
            int i = nu++;                          /* insertion sort by name */
            while (i > 0 && ncmp(names[i - 1], n) > 0) { for (int k = 0; k < NAMEN; k++) names[i][k] = names[i - 1][k]; i--; }
            int k = 0; for (; k < NAMEN - 1 && n[k]; k++) names[i][k] = n[k];
            names[i][k] = 0;
        }
        r = r8_find_next(find);
    }
    r8_find_close(find);
    for (int i = 0; i < nu; i++) {
        int j = 0; for (; names[i][j] && names[i][j] != '.'; j++) labels[i][j] = names[i][j];
        labels[i][j] = 0;
        const char *p = "A:\\R8UTILS\\"; int k = 0;
        for (; *p; ) paths[i][k++] = *p++;
        for (const char *n = names[i]; *n; ) paths[i][k++] = *n++;
        paths[i][k] = 0;
    }
}

static u32 crc32(const u8 *p, u32 n)               /* zlib CRC-32 */
{
    u32 c = 0xFFFFFFFFu;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1))); }
    return ~c;
}
static int load(const char *path, util_hdr *h)     /* 0 ok, 1 can't open, 2 invalid */
{
    int fd; u32 got;
    if (r8_open(&fd, path, 0, 0)) return 1;
    int bad = r8_read(fd, h, sizeof *h, &got) || got != sizeof *h
        || h->magic != UTIL_MAGIC || h->version != UTIL_VERSION || h->len == 0
        || h->load < UTILS_AT || h->load + h->len < h->load || h->top < h->load + h->len || h->top > UTIL_HI
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
static void run(int i)
{
    util_hdr h;
    if (i < 0 || i >= nu) return;
    int e = load(paths[i], &h);
    if (e) { r8_popup(e == 1 ? R8_STR_NO_FILE : R8_STR_INVALID_FILE); return; }
    ((util_fn)h.entry)(paths[i]);
}
/* one handler per item: the menu engine calls the record's handler with no arguments we rely on */
#define H(n) static void h##n(void) { run(n); }
H(0) H(1) H(2) H(3) H(4) H(5) H(6) H(7) H(8) H(9) H(10) H(11) H(12) H(13) H(14) H(15)
static void (*const handlers[MAXU])(void) = { h0, h1, h2, h3, h4, h5, h6, h7, h8, h9, h10, h11, h12, h13, h14, h15 };

u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    scan();
    if (nu == 0) { r8_popup("NO UTILITIES"); return 0; }
    for (int i = 0; i < nu; i++) {
        u32 *r = recs[i];
        for (int k = 0; k < 20; k++) r[k] = 0;
        r[0] = (u32)labels[i]; r[4] = (u32)handlers[i]; r[6] = 1;
        r[12] = r[13] = r[14] = r[15] = REC_PAD_STR;
        list[i] = (u32)r;
    }
    list[nu] = 0;
    state[0] = state[1] = 0;
    for (int k = 0; k < 12; k++) desc[k] = 0;
    desc[0] = (u32)nu; desc[1] = (u32)title; desc[5] = (u32)&state[0]; desc[6] = (u32)&state[1];
    desc[7] = 0x10013; desc[8] = (u32)list; desc[9] = SD_CARD_CALLBACK;
    r8_menu_engine(desc, 0, 0, 0);
    return 0;
}
