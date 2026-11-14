/* UTILDIAG (kept for reference; was the first UTILMENU.SYS): started by RUN UTIL. Lists A:\R8UTILS with the stock find functions and saves what
   it saw to A:\R8UTILS\UTILDIAG.BIN, then shows a popup with the count. This pins down the find structure's layout
   (name, attributes, size) on the hardware before the real menu is built on it.
   UTILDIAG.BIN: 'R8FD', n entries, first result, last result, then n x (result, 0x1FC bytes of the find structure). */
#include "r8.h"
#define MAXE 24
#define FSZ  0x1FC
static u8 find[0x200];                             /* static: the menu task's stack is small */
static u32 hdr[4];
static u8 ents[MAXE][4 + FSZ];
static char msg[40];
u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    int n = 0, r = r8_find_first("A:\\R8UTILS\\*.*", 0x17, find), first = r;
    while (r == 0 && n < MAXE) {
        *(int *)ents[n] = r;
        for (int i = 0; i < FSZ; i++) ents[n][4 + i] = find[i];
        n++;
        r = r8_find_next(find);
    }
    r8_find_close(find);
    hdr[0] = 0x44463852u; hdr[1] = (u32)n; hdr[2] = (u32)first; hdr[3] = (u32)r;
    int fd; u32 w;
    if (!r8_open(&fd, "A:\\R8UTILS\\UTILDIAG.BIN", 0x101, 0x80)) {
        r8_write(fd, hdr, sizeof hdr, &w);
        r8_write(fd, ents, (u32)n * (4 + FSZ), &w);
        r8_close(fd);
    }
    const char *t = "UTILMENU: n files";
    int k = 0; for (; t[k] && t[k] != 'n'; k++) msg[k] = t[k];
    if (n >= 10) msg[k++] = (char)('0' + n / 10);
    msg[k++] = (char)('0' + n % 10);
    for (const char *s = " files"; *s; ) msg[k++] = *s++;
    msg[k] = 0;
    r8_popup(msg);
    return 0;
}
