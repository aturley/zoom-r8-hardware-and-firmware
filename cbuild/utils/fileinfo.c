/* FILEINFO.BIN: browse the card (browse.c) and show a file's size, modification date and time, and attributes; for
   a WAV also its rate, bits (FLOAT for float), channels and length. Values longer than 10 characters scroll only on
   the selected row, so each is kept short. The information is a menu of
   label/value rows titled with the file's (long) name; EXIT goes back to the folder. */
#include "browse.h"

static char val[8][24];
static const char *labels[8], *vals[8];
static char title[48];
static u8 hdr[64];

static u32 le32(const u8 *p) { return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }
static u32 le16(const u8 *p) { return p[0] | p[1] << 8; }
static int is_wav(const char *path)
{
    int k = (int)r8_strlen(path);
    return k > 4 && path[k - 4] == '.' && (path[k - 3] | 0x20) == 'w' && (path[k - 2] | 0x20) == 'a' && (path[k - 1] | 0x20) == 'v';
}
static int rd(int fd, u32 off, void *buf, u32 n)
{
    u32 got;
    return r8_seek(fd, (s32)off, 2) == 0 && r8_read(fd, buf, n, &got) == 0 && got == n;
}
/* fills the RATE, BITS, CHANNELS and LENGTH values from a WAV's fmt and data chunks; returns how many it wrote */
static char (*v)[24];
static int wav_lines(const char *path)
{
    int fd;
    if (r8_open(&fd, path, 0, 0)) return 0;
    u32 rate = 0, bits = 0, ch = 0, fmt = 0, data = 0, off = 12;
    int ok = rd(fd, 0, hdr, 12) && le32(hdr) == 0x46464952u && le32(hdr + 8) == 0x45564157u;   /* RIFF....WAVE */
    for (int k = 0; ok && k < 32 && !(rate && data); k++) {
        if (!rd(fd, off, hdr, 8)) break;
        u32 id = le32(hdr), sz = le32(hdr + 4);
        if (id == 0x20746d66u && rd(fd, off + 8, hdr, 16)) {                                    /* "fmt " */
            fmt = le16(hdr); ch = le16(hdr + 2); rate = le32(hdr + 4); bits = le16(hdr + 14);
            if (fmt == 0xFFFE && sz >= 40 && rd(fd, off + 8 + 24, hdr, 2)) fmt = le16(hdr);     /* EXTENSIBLE */
        } else if (id == 0x61746164u) data = sz;                                                  /* "data" */
        off += 8 + sz + (sz & 1);
    }
    r8_close(fd);
    if (!rate || !ch || !bits) return 0;
    put_u(v[0], rate, 0);                                                                       /* RATE */
    put_s(put_u(v[1], bits, 0), fmt == 3 ? " FLOAT" : "");                                      /* BITS */
    if (ch == 1) put_s(v[2], "MONO"); else if (ch == 2) put_s(v[2], "STEREO"); else put_u(v[2], ch, 0);
    if (!data) return 3;
    u32 frames = data / (ch * ((bits + 7) / 8));
    u32 cs = frames / rate * 100 + frames % rate * 100 / rate;                                  /* 1/100 s */
    char *e = put_u(v[3], cs / 6000, 0); e = put_s(e, ":"); e = put_u(e, cs / 100 % 60, 2);
    e = put_s(e, "."); put_u(e, cs % 100, 2);
    return 4;
}
static void info(const char *path, const u8 *f)
{
    int n = 0;
    char *e;
    labels[n] = "SIZE"; put_u(val[n], le32(f + F_SIZE), 0); n++;
    labels[n] = "DATE"; e = put_u(val[n], le16(f + F_MDATE + 2), 4); e = put_s(e, "-"); e = put_u(e, f[F_MDATE + 1], 2);
    e = put_s(e, "-"); put_u(e, f[F_MDATE], 2); n++;
    labels[n] = "TIME"; e = put_u(val[n], f[F_MDATE + 6], 2); e = put_s(e, ":"); e = put_u(e, f[F_MDATE + 7], 2);
    e = put_s(e, ":"); put_u(e, f[F_MDATE + 8], 2); n++;
    u8 a = f[F_ATTR];
    if (a & 0x07) {
        labels[n] = "ATTR"; e = val[n]; *e = 0;
        if (a & 1) e = put_s(e, "READ-ONLY ");
        if (a & 2) e = put_s(e, "HIDDEN ");
        if (a & 4) e = put_s(e, "SYSTEM ");
        e[-1] = 0; n++;
    }
    if (is_wav(path)) {
        static const char *const wl[4] = { "RATE", "BITS", "CHANNELS", "LENGTH" };
        v = val + n;
        int k = wav_lines(path);
        for (int i = 0; i < k; i++) labels[n + i] = wl[i];
        n += k;
    }
    for (int i = 0; i < n; i++) vals[i] = val[i];
    const char *nm = entry_name(f);
    int k = 0; for (; nm[k] && k < (int)sizeof title - 1; k++) title[k] = nm[k];
    title[k] = 0;
    show_info(title, labels, vals, n);
}

u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    browse("A:", "FILE INFO", info);
    return 0;
}
