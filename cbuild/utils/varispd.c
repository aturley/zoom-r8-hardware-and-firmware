/* VARISPD.BIN: varispeed, as the looper's speed faders: pick a WAV (browse.c), then set SPEED with the wheel,
   -4.0x to +4.0x in steps of 0.1 (0 is skipped): +2.0x plays twice as fast (an octave up, half as long), +0.5x an
   octave down and twice as long, negative speeds play the file backwards. The row below shows the resulting length.
   ENTER makes the file: a new one in the same folder, in the source's format (rate, bits, channels; integer or
   float), named from the source's long name and the speed: "Bike loop.wav" at +1.5x -> "Bike loop +1.5x.wav", or
   "Bike loop +1.5x (2).wav" ... (9) if that exists; popup SAVED. If the file system will not create that name, an
   8.3 name from the source's 8.3 name: TRACK01.WAV -> TRACK01V.WAV, or TRACK0V2.WAV ... TRACK0V9.WAV; popup SAVED and
   the name. The source is never changed. The menu stays open for another speed; EXIT returns to the folder.

   Resampling: output frame n is taken at input position n * |speed| (in tenths), kept exactly as an integer and a
   remainder in tenths, so the filter is a table of at most 10 phases (only those reached are built). Each phase is
   a Kaiser-windowed sinc (16 zero crossings each side, beta 8) with the cutoff at 0.9 of the lower of the two
   Nyquist frequencies, so speeding up does not alias; each phase is normalised to unity gain at DC. For negative
   speeds the input is read from the end. Samples outside the file count as silence. Output of 8 and 16 bits gets
   TPDF dither; all output is clipped. */
#include "browse.h"

#define CH_MAX 8
#define ZC     16                                  /* zero crossings each side */
#define BETA   8.0
#define TMAX   144                                 /* taps at 4x: 2 * ceil(16 / (0.9 / 4)) */
#define SMAX   40                                  /* speeds -4.0x .. +4.0x, in tenths */
#define IBN    (32768)                             /* input floats */
#define RAWN   32760                               /* bytes per file read / write, as REVERSE uses */


/* the picked file */
static char src[BROWSE_PATH], dst[BROWSE_PATH + 128], title[48], lname[100];
static const char *dst_name;
static int dst_long;
static u32 rate, bits, ch, fmt, ba, doff, dlen, nin;

static int speed = 10, last_speed = 10;           /* tenths; kept between files */

static float tab[10][TMAX];
static float ib[IBN];
static u8 rb[RAWN], ob[RAWN], hdr[64];

static u32 le32(const u8 *p) { return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }
static u32 le16(const u8 *p) { return p[0] | p[1] << 8; }
static void put32(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); p[2] = (u8)(v >> 16); p[3] = (u8)(v >> 24); }
static void put16(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); }

/* ---- maths (no runtime library; division of doubles is slow here, so the loops multiply) ---- */
static const double inv[] = {                      /* 1 / (2i (2i + 1)) for the sine series */
    0, 1.0 / 6, 1.0 / 20, 1.0 / 42, 1.0 / 72, 1.0 / 110, 1.0 / 156, 1.0 / 210, 1.0 / 272, 1.0 / 342 };
static double sinpi(double x)                      /* sin(pi x) */
{
    double k = (double)(long long)(x * 0.5);
    x -= 2.0 * k;                                  /* (-2, 2) */
    if (x > 1.0) x -= 2.0; else if (x < -1.0) x += 2.0;
    if (x > 0.5) x = 1.0 - x; else if (x < -0.5) x = -1.0 - x;
    double y = 3.14159265358979323846 * x, y2 = y * y, t = y, s = y;
    for (int i = 1; i < 10; i++) { t *= -y2 * inv[i]; s += t; }
    return s;
}
static double sqrt01(double a)                     /* square root for a in [0, 1] */
{
    if (a <= 0.0) return 0.0;
    double sc = 1.0;
    while (a < 0.25) { a *= 4.0; sc *= 0.5; }      /* now a in [0.25, 1]: Newton from 1 converges in 6 */
    double x = 1.0;
    for (int i = 0; i < 6; i++) x = 0.5 * (x + a / x);
    return x * sc;
}
static double ik2[30];                             /* 1 / k^2 */
static double i0(double x)                         /* modified Bessel function of order 0 (x <= 8: 30 terms) */
{
    double s = 1.0, t = 1.0, q = x * x * 0.25;
    for (int k = 1; k < 30; k++) { t *= q * ik2[k]; s += t; }
    return s;
}
/* the filter for |speed| = step / 10: taps 2h, cutoff fc (1 = Nyquist) */
static int make_table(int step)
{
    double fc = 0.9 * (step > 10 ? 10.0 / step : 1.0);
    int h = (int)(ZC / fc); if (h < ZC / fc) h++;
    for (int k = 1; k < 30; k++) ik2[k] = 1.0 / ((double)k * k);
    double ib0 = 1.0 / i0(BETA), rh = 1.0 / h, pifc = 3.14159265358979323846 * fc;
    int g = step, m = 10;                          /* positions reached: multiples of gcd(step, 10) tenths */
    while (m) { int t = g % m; g = m; m = t; }
    for (int r = 0; r < 10; r += g) {
        double sum = 0.0, v[TMAX];
        for (int k = 0; k < 2 * h; k++) {
            double t = (k - h + 1) - r * 0.1;      /* input index minus position */
            double x = t * rh, w = x <= -1.0 || x >= 1.0 ? 0.0 : i0(BETA * sqrt01(1.0 - x * x)) * ib0;
            double s = t == 0.0 ? 1.0 : sinpi(fc * t) / (pifc * t);
            v[k] = fc * s * w; sum += v[k];
        }
        double rs = 1.0 / sum;
        for (int k = 0; k < 2 * h; k++) tab[r][k] = (float)(v[k] * rs);
    }
    return h;
}

/* ---- the source ---- */
static int rd(int fd, u32 off, void *buf, u32 n)
{
    u32 got;
    return r8_seek(fd, (s32)off, 2) == 0 && r8_read(fd, buf, n, &got) == 0 && got == n;
}
/* reads the format and where the audio is; 0 if it is not a WAV we can do */
static int parse(const char *path)
{
    int fd;
    if (r8_open(&fd, path, 0, 0)) return 0;
    u32 off = 12;
    rate = bits = ch = fmt = doff = dlen = 0;
    int ok = rd(fd, 0, hdr, 12) && le32(hdr) == 0x46464952u && le32(hdr + 8) == 0x45564157u;   /* RIFF....WAVE */
    for (int k = 0; ok && k < 64 && !(rate && doff); k++) {
        if (!rd(fd, off, hdr, 8)) break;
        u32 id = le32(hdr), sz = le32(hdr + 4);
        if (id == 0x20746d66u && sz >= 16 && rd(fd, off + 8, hdr, 16)) {                       /* "fmt " */
            fmt = le16(hdr); ch = le16(hdr + 2); rate = le32(hdr + 4); bits = le16(hdr + 14);
            if (fmt == 0xFFFE && sz >= 40 && rd(fd, off + 8 + 24, hdr, 2)) fmt = le16(hdr);    /* EXTENSIBLE */
        } else if (id == 0x61746164u) { doff = off + 8; dlen = sz; }                          /* "data" */
        off += 8 + sz + (sz & 1);
    }
    r8_close(fd);
    if (!rate || !doff || ch < 1 || ch > CH_MAX) return 0;
    if (!(fmt == 1 && (bits == 8 || bits == 16 || bits == 24 || bits == 32)) && !(fmt == 3 && bits == 32)) return 0;
    ba = ch * bits / 8;
    nin = dlen / ba;
    return nin > 0;
}
static u32 out_frames(int sp)                       /* ceil(nin / |speed|) */
{
    u32 step = (u32)(sp < 0 ? -sp : sp);
    return (u32)(((unsigned long long)nin * 10 + step - 1) / step);
}

/* ---- conversion ---- */
static float to_f(const u8 *s)
{
    switch (bits) {
    case 8:  return (float)((int)s[0] - 128) * (1.0f / 128);
    case 16: return (float)(short)(s[0] | s[1] << 8) * (1.0f / 32768);
    case 24: return (float)((int)((u32)s[0] << 8 | (u32)s[1] << 16 | (u32)s[2] << 24) >> 8) * (1.0f / 8388608);
    default:
        if (fmt == 3) { union { u32 u; float f; } v; v.u = le32(s); return v.f; }
        return (float)(int)le32(s) * (1.0f / 2147483648.0f);
    }
}
static u32 seed = 12345;
static float rnd(void) { seed = seed * 1664525u + 1013904223u; return (float)(seed >> 8) * (1.0f / 16777216); }
static void from_f(u8 *d, float v)
{
    if (fmt == 3) { union { u32 u; float f; } x; x.f = v; put32(d, x.u); return; }
    float full = bits == 8 ? 128.0f : bits == 16 ? 32768.0f : bits == 24 ? 8388608.0f : 2147483648.0f;
    float x = v * full;
    if (bits <= 16) x += rnd() - rnd();            /* TPDF dither, +-1 LSB */
    x += x >= 0 ? 0.5f : -0.5f;                    /* round */
    if (x >= full - 1) x = full - 1; else if (x <= -full) x = -full;
    int i = bits == 32 && x >= 2147483520.0f ? 0x7FFFFFFF : (int)x;
    if (bits == 8) d[0] = (u8)(i + 128);
    else if (bits == 16) put16(d, (u32)i);
    else if (bits == 24) { d[0] = (u8)i; d[1] = (u8)(i >> 8); d[2] = (u8)(i >> 16); }
    else put32(d, (u32)i);
}

/* ---- the work ---- */
static int fin, fout;
static u32 rpos, rlen, rleft, opos;
static int rev;
/* input frame v of the stream (the file, or the file backwards), read in order; silence outside the file */
static int in_frame(long long v, float *dst)
{
    if (v < 0 || v >= nin) { for (u32 c = 0; c < ch; c++) dst[c] = 0.0f; return 1; }
    if (rpos >= rlen) {
        u32 n = RAWN / ba * ba, got;
        if (n > rleft) n = rleft;
        if (rev && r8_seek(fin, (s32)(doff + rleft - n), 2)) return 0;   /* backwards: the block before the last */
        if (r8_read(fin, rb, n, &got) || got != n) return 0;
        rpos = 0; rlen = n; rleft -= n;
    }
    const u8 *f = rb + (rev ? rlen - ba - rpos : rpos);
    for (u32 c = 0; c < ch; c++) dst[c] = to_f(f + c * (bits / 8));
    rpos += ba;
    return 1;
}
static int flush(void)
{
    u32 n;
    if (opos && (r8_write(fout, ob, opos, &n) || n != opos)) return 0;
    opos = 0; return 1;
}
/* the filter sums for mono and stereo, with separate accumulators so the compiler can pipeline them (the taps
   count, 2h, is even) */
static float dot1(const float *restrict x, const float *restrict t, int n)
{
    float a0 = 0.0f, a1 = 0.0f;
    #pragma MUST_ITERATE(2, , 2)
    for (int k = 0; k < n; k += 2) { a0 += x[k] * t[k]; a1 += x[k + 1] * t[k + 1]; }
    return a0 + a1;
}
static void dot2(const float *restrict x, const float *restrict t, int n, float *y)
{
    float l0 = 0.0f, l1 = 0.0f, r0 = 0.0f, r1 = 0.0f;
    #pragma MUST_ITERATE(2, , 2)
    for (int k = 0; k < n; k += 2) {
        l0 += x[2 * k] * t[k]; r0 += x[2 * k + 1] * t[k];
        l1 += x[2 * k + 2] * t[k + 1]; r1 += x[2 * k + 3] * t[k + 1];
    }
    y[0] = l0 + l1; y[1] = r0 + r1;
}
static int resample(u32 step, u32 nout, int h)
{
    u32 cap = IBN / ch;
    long long b0 = -h, i0 = 0;                     /* ib holds frames b0 .. b0 + bn - 1 */
    u32 bn = 0, r = 0;
    float y[CH_MAX];
    for (u32 n = 0; n < nout; n++) {
        long long lo = i0 - h + 1, hi = i0 + h + 1;
        if (hi > b0 + bn) {                        /* move the window: keep from lo, then fill up */
            while (b0 + bn < lo) { if (!in_frame(b0 + bn, y)) return 0; bn++; }   /* (not reached: the window is wide) */
            u32 keep = (u32)(b0 + bn > lo ? b0 + bn - lo : 0), from = (u32)(lo - b0);
            if (keep) for (u32 k = 0; k < keep * ch; k++) ib[k] = ib[from * ch + k];
            b0 = lo; bn = keep;
            while (bn < cap) { if (!in_frame(b0 + bn, ib + bn * ch)) return 0; bn++; }
        }
        const float *x = ib + (u32)(lo - b0) * ch, *t = tab[r];
        if (ch == 1) y[0] = dot1(x, t, 2 * h);
        else if (ch == 2) dot2(x, t, 2 * h, y);
        else {
            for (u32 c = 0; c < ch; c++) y[c] = 0.0f;
            for (int k = 0; k < 2 * h; k++, x += ch) {
                float tk = t[k];
                for (u32 c = 0; c < ch; c++) y[c] += x[c] * tk;
            }
        }
        if (opos + ba > RAWN / ba * ba && !flush()) return 0;
        for (u32 c = 0; c < ch; c++) from_f(ob + opos + c * (bits / 8), y[c]);
        opos += ba;
        r += step; i0 += r / 10; r %= 10;
    }
    return flush();
}

static int exists(const char *path)
{
    int fd;
    if (r8_open(&fd, path, 0, 0)) return 0;
    r8_close(fd); return 1;
}
static int folder_len(void)                        /* src up to and including the last '\\' */
{
    int k = (int)r8_strlen(src);
    while (k > 0 && src[k - 1] != '\\') k--;
    return k;
}
static char *put_speed(char *d, int sp)            /* +1.5x */
{
    *d++ = sp < 0 ? '-' : '+';
    if (sp < 0) sp = -sp;
    d = put_u(d, (u32)sp / 10, 0); d = put_s(d, "."); d = put_u(d, (u32)sp % 10, 0);
    return put_s(d, "x");
}
/* dst = the folder of src + "<long name without extension> +1.5x.WAV", then " (2)" ... " (9)" before the extension
   if that exists. 0 if all are taken. */
static int make_long_dst(int sp)
{
    int slash = folder_len(), n = (int)r8_strlen(lname), e = n;
    while (e > 0 && lname[e - 1] != '.') e--;
    const char *ext = e ? lname + e - 1 : ".WAV";
    if (!e) e = n + 1;                             /* no extension */
    for (int v = 1; v <= 9; v++) {
        char *d = dst;
        for (int i = 0; i < slash; i++) *d++ = src[i];
        dst_name = d;
        for (int i = 0; i < e - 1; i++) *d++ = lname[i];
        *d++ = ' ';
        d = put_speed(d, sp);
        if (v > 1) { d = put_s(d, " ("); d = put_u(d, (u32)v, 0); d = put_s(d, ")"); }
        put_s(d, ext);
        if (!exists(dst)) return 1;
    }
    return 0;
}
/* the fallback: dst = the folder of src + a new 8.3 name from src's 8.3 stem (TRACK01V.WAV, TRACK0V2.WAV ...) */
static int make_short_dst(void)
{
    int slash = folder_len();
    char stem[9]; int n = 0;
    for (int i = slash; src[i] && src[i] != '.' && n < 8; i++) stem[n++] = src[i];
    for (int v = 1; v <= 9; v++) {
        int i = 0;
        for (; i < slash; i++) dst[i] = src[i];
        dst_name = dst + i;
        int m = v == 1 ? (n < 7 ? n : 7) : (n < 6 ? n : 6);
        for (int j = 0; j < m; j++) dst[i++] = stem[j];
        dst[i++] = 'V';
        if (v > 1) dst[i++] = (char)('0' + v);
        put_s(dst + i, ".WAV");
        if (!exists(dst)) return 1;
    }
    return 0;
}

static void go(int row)
{
    (void)row;
    int sp = speed;
    u32 nout = out_frames(sp), step = (u32)(sp < 0 ? -sp : sp);
    if ((unsigned long long)nout * ba > 0xFFFFFFFFull - 64) { r8_popup("TOO LONG"); return; }
    if (r8_card_check(11)) return;
    r8_pre_msg();
    r8_show_msg(R8_STR_PLEASE_WAIT);
    int ok = 0, noname = 0, h = make_table((int)step);
    if (!r8_open(&fin, src, 0, 0)) {
        /* a long name first; if the file system will not create it, an 8.3 name */
        int made = (dst_long = make_long_dst(sp)) && !r8_open(&fout, dst, 0x101, 0x80);
        if (!made) {
            dst_long = 0;
            int free = make_short_dst();
            noname = !free;
            made = free && !r8_open(&fout, dst, 0x101, 0x80);
        }
        if (made) {
            u32 data = nout * ba, n;
            u8 *w = hdr;
            put32(w, 0x46464952u); put32(w + 4, 36 + data + (data & 1)); put32(w + 8, 0x45564157u);
            put32(w + 12, 0x20746d66u); put32(w + 16, 16);
            put16(w + 20, fmt); put16(w + 22, ch); put32(w + 24, rate); put32(w + 28, rate * ba); put16(w + 32, ba);
            put16(w + 34, bits); put32(w + 36, 0x61746164u); put32(w + 40, data);
            rpos = rlen = opos = 0; rleft = nin * ba; rev = sp < 0;
            ok = r8_write(fout, hdr, 44, &n) == 0 && n == 44 && r8_seek(fin, (s32)doff, 2) == 0
                 && resample(step, nout, h);
            if (ok && (data & 1)) { ob[0] = 0; opos = 1; ok = flush(); }
            if (r8_close(fout)) ok = 0;
            if (!ok) r8_delete(dst);
        }
        r8_close(fin);
    }
    r8_dialog_close(ok);
    if (!ok) { r8_popup(noname ? "NO FREE NAME" : R8_STR_ACCESS_ERROR); return; }
    static char msg[24];
    if (dst_long) r8_popup("SAVED");               /* the long name would not fit */
    else { put_s(put_s(msg, "SAVED "), dst_name); r8_popup(msg); }
}

static char *put_len(char *d, u32 frames)          /* m:ss.cc */
{
    u32 cs = frames / rate * 100 + frames % rate * 100 / rate;
    d = put_u(d, cs / 6000, 0); d = put_s(d, ":"); d = put_u(d, cs / 100 % 60, 2);
    d = put_s(d, "."); return put_u(d, cs % 100, 2);
}
/* the SPEED and LENGTH rows */
static char vbuf[16];
static const char *speed_text(const u32 *s) { put_speed(vbuf, *(const int *)s[3]); return vbuf; }
static const char *length_text(const u32 *s) { (void)s; put_len(vbuf, out_frames(speed)); return vbuf; }
static void speed_edited(const u32 *s)             /* the wheel skips 0 */
{
    int *v = (int *)s[3];
    if (*v == 0) *v = last_speed > 0 ? -1 : 1;
    last_speed = *v;
}
static const menu_item rows[] = {
    { "SPEED",  speed_text,  0, &speed, -SMAX, SMAX, speed_edited },
    { "LENGTH", length_text, 0, 0, 0, 0, 0 },
};

static void picked(const char *path, const u8 *e)
{
    put_s(src, path);
    const char *ln = entry_name(e);
    int k0 = 0; for (; ln[k0] && k0 < (int)sizeof lname - 1; k0++) lname[k0] = ln[k0];
    lname[k0] = 0;
    if (!parse(src)) { r8_popup(R8_STR_INVALID_FILE); return; }
    int k = 0; for (; ln[k] && k < (int)sizeof title - 1; k++) title[k] = ln[k];
    title[k] = 0;
    show_items(title, rows, 2, go, 0);
}

u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    browse("A:", "VARISPEED", picked);
    return 0;
}
