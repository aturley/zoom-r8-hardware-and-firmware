/* LISTDIAG.BIN: saves the raw find results for A:\*.* and A:\ZOOM_R8\*.* to A:\R8UTILS\ROOTDIAG.DAT and ZOOMDIAG.DAT
   ('R8FD', n, first result, last result, then n x (result, 0x1FC bytes of the find structure)), then a popup with
   both counts. For checking what FILEINFO's picker is given. */
#include "browse.h"
#define MAXE 48
#define FSZ  0x1FC
static u8 find[0x200];
static u32 hdr[4];
static u8 ents[MAXE][4 + FSZ];
static char msg[40];
static int dump(const char *pat, const char *out)
{
    int n = 0, r = r8_find_first(pat, 0x17, find), first = r;
    while (r == 0 && n < MAXE) {
        *(int *)ents[n] = r;
        for (int i = 0; i < FSZ; i++) ents[n][4 + i] = find[i];
        n++;
        r = r8_find_next(find);
    }
    r8_find_close(find);
    hdr[0] = 0x44463852u; hdr[1] = (u32)n; hdr[2] = (u32)first; hdr[3] = (u32)r;
    int fd; u32 w;
    if (!r8_open(&fd, out, 0x101, 0x80)) {
        r8_write(fd, hdr, sizeof hdr, &w);
        r8_write(fd, ents, (u32)n * (4 + FSZ), &w);
        r8_close(fd);
    }
    return n;
}
u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    int a = dump("A:\\*.*", "A:\\R8UTILS\\ROOTDIAG.DAT");
    int z = dump("A:\\ZOOM_R8\\*.*", "A:\\R8UTILS\\ZOOMDIAG.DAT");
    char *e = put_s(msg, "ROOT "); e = put_u(e, (u32)a, 0); e = put_s(e, " ZOOM "); put_u(e, (u32)z, 0);
    r8_popup(msg);
    return 0;
}
