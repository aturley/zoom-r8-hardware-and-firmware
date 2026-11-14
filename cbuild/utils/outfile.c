/* see outfile.h */
#include "outfile.h"
#include "browse.h"

static int exists(const char *path)
{
    int fd;
    if (r8_open(&fd, path, 0, 0)) return 0;
    r8_close(fd); return 1;
}
static int folder_len(const char *src)
{
    int k = (int)r8_strlen(src);
    while (k > 0 && src[k - 1] != '\\') k--;
    return k;
}
int out_create(int *fd, char *dst, const char **name, const char *src, const char *lname, const char *suffix, char tag)
{
    int slash = folder_len(src), n = (int)r8_strlen(lname), e = n;
    while (e > 0 && lname[e - 1] != '.') e--;
    const char *ext = e ? lname + e - 1 : ".WAV";
    if (!e) e = n + 1;
    for (int v = 1; v <= 9; v++) {                 /* "<long name><suffix>.wav", " (2)" ... " (9)" */
        char *d = dst;
        for (int i = 0; i < slash; i++) *d++ = src[i];
        *name = d;
        for (int i = 0; i < e - 1 && i < 100; i++) *d++ = lname[i];
        d = put_s(d, suffix);
        if (v > 1) { d = put_s(d, " ("); d = put_u(d, (u32)v, 0); d = put_s(d, ")"); }
        put_s(d, ext);
        if (exists(dst)) continue;
        if (!r8_open(fd, dst, 0x101, 0x80)) return 1;
        break;                                     /* the file system will not make this name: 8.3 instead */
    }
    char stem[9]; int m = 0;
    for (int i = slash; src[i] && src[i] != '.' && m < 8; i++) stem[m++] = src[i];
    for (int v = 1; v <= 9; v++) {                 /* TRACK01R.WAV, TRACK0R2.WAV ... */
        int i = 0;
        for (; i < slash; i++) dst[i] = src[i];
        *name = dst + i;
        int k = v == 1 ? (m < 7 ? m : 7) : (m < 6 ? m : 6);
        for (int j = 0; j < k; j++) dst[i++] = stem[j];
        dst[i++] = tag;
        if (v > 1) dst[i++] = (char)('0' + v);
        put_s(dst + i, ".WAV");
        if (exists(dst)) continue;
        return r8_open(fd, dst, 0x101, 0x80) ? 0 : 2;
    }
    return 0;
}
