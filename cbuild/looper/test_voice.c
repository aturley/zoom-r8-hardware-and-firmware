/* Host test of the loop voice: cc -O2 -o out/test_voice test_voice.c voice.c -lm && out/test_voice [outdir] */
#include "voice.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int fails;
static void check(const char *n, int ok) { printf("%s %s\n", ok ? "PASS" : "FAIL", n); if (!ok) fails++; }
#define FS 44100
#define PI 3.14159265358979

/* amplitude of frequency f in x[0..n) (Goertzel) */
static double tone(const float *x, int n, double f)
{
    double w = 2 * PI * f / FS, c = 2 * cos(w), s1 = 0, s2 = 0;
    for (int i = 0; i < n; i++) { double s = x[i] + c * s1 - s2; s2 = s1; s1 = s; }
    return 2 * sqrt(fmax(0, s1 * s1 + s2 * s2 - c * s1 * s2)) / n;
}
/* residual after removing the best-fit sine at f: error relative to the tone, in dB */
static double snr(const float *x, int n, double f)
{
    double w = 2 * PI * f / FS, sc = 0, ss = 0;
    for (int i = 0; i < n; i++) { sc += x[i] * cos(w * i); ss += x[i] * sin(w * i); }
    sc *= 2.0 / n; ss *= 2.0 / n;
    double e = 0, p = 0;
    for (int i = 0; i < n; i++) { double y = sc * cos(w * i) + ss * sin(w * i); e += (x[i] - y) * (x[i] - y); p += y * y; }
    return 10 * log10(p / e);
}
static void wav(const char *path, const float *l, const float *r, int n)
{
    FILE *f = fopen(path, "wb"); if (!f) return;
    unsigned sz = n * 4, v; unsigned short h;
    fwrite("RIFF", 1, 4, f); v = 36 + sz; fwrite(&v, 4, 1, f); fwrite("WAVEfmt ", 1, 8, f);
    v = 16; fwrite(&v, 4, 1, f); h = 1; fwrite(&h, 2, 1, f); h = 2; fwrite(&h, 2, 1, f);
    v = FS; fwrite(&v, 4, 1, f); v = FS * 4; fwrite(&v, 4, 1, f); h = 4; fwrite(&h, 2, 1, f); h = 16; fwrite(&h, 2, 1, f);
    fwrite("data", 1, 4, f); fwrite(&sz, 4, 1, f);
    for (int i = 0; i < n; i++) {
        short a = (short)fmax(-32768, fmin(32767, l[i] * 32767)), b = (short)fmax(-32768, fmin(32767, r[i] * 32767));
        fwrite(&a, 2, 1, f); fwrite(&b, 2, 1, f);
    }
    fclose(f);
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : ".";
    /* a 1 kHz sine loop, exactly 441 cycles in 19404 frames... use 44100 frames = 1000 whole cycles: seamless */
    int N = FS; short *sine = malloc(N * 2);
    for (int i = 0; i < N; i++) sine[i] = (short)lrint(16000 * sin(2 * PI * 1000.0 * i / FS));
    int M = FS / 2; float *l = calloc(M, 4), *r = calloc(M, 4);
    voice_t v;
    static const float sp[] = { 1, 2, 0.5f, -1, -2, 4, -4, 0.25f, 3.3f };
    for (unsigned k = 0; k < sizeof sp / sizeof sp[0]; k++) {
        voice_init(&v, sine, N, 1, FS, FS); v.smooth = 1; voice_speed(&v, sp[k]);
        memset(l, 0, M * 4); memset(r, 0, M * 4);
        for (int j = 0; j < M; j += 32) voice_render(&v, l + j, r + j, 32);
        double f = 1000 * fabs(sp[k]), a = tone(l, M, f), s = snr(l, M, f);
        char name[96]; snprintf(name, sizeof name, "speed %+.2f: 1 kHz loop plays at %.0f Hz, level %.3f, interpolation error %.1f dB below", sp[k], f, a, s);
        check(name, fabs(a - 16000 / 32768.0) < 0.01 && s > (fabs(sp[k]) < 1 ? 50 : 40));
    }
    /* stereo, different rates: 48 kHz file at speed 1 -> its pitch is kept; channels stay separate */
    short *st = malloc(2 * 48000 * 2);
    for (int i = 0; i < 48000; i++) { st[2 * i] = (short)lrint(16000 * sin(2 * PI * 600.0 * i / 48000)); st[2 * i + 1] = (short)lrint(8000 * sin(2 * PI * 1500.0 * i / 48000)); }
    voice_init(&v, st, 48000, 2, 48000, FS); v.smooth = 1; voice_speed(&v, 1);
    memset(l, 0, M * 4); memset(r, 0, M * 4);
    for (int j = 0; j < M; j += 32) voice_render(&v, l + j, r + j, 32);
    check("48 kHz stereo file at speed 1: left 600 Hz, right 1500 Hz, no leakage",
          fabs(tone(l, M, 600) - 16000 / 32768.0) < 0.01 && fabs(tone(r, M, 1500) - 8000 / 32768.0) < 0.01 && tone(l, M, 1500) < 1e-3 && tone(r, M, 600) < 1e-3);
    /* reverse: a ramp plays backwards and wraps */
    short *ramp = malloc(1000 * 2); for (int i = 0; i < 1000; i++) ramp[i] = (short)(i * 30 - 15000);
    voice_init(&v, ramp, 1000, 1, FS, FS); v.smooth = 1; v.pos = (long long)500 << 32; voice_speed(&v, -1);
    memset(l, 0, M * 4); memset(r, 0, M * 4);
    voice_render(&v, l, r, 32);
    int down = 1; for (int j = 1; j < 32; j++) if (!(l[j] < l[j - 1])) down = 0;
    check("speed -1: a rising ramp plays falling", down && fabs(l[0] * 32768 - ramp[500]) < 1);
    for (int j = 0; j < 3000; j += 32) voice_render(&v, l, r, 32);
    check("position stays inside the loop after wrapping backwards", v.pos >= 0 && v.pos < ((long long)1000 << 32));
    /* smoothing: a jump in the target moves the step gradually */
    voice_init(&v, sine, N, 1, FS, FS); voice_speed(&v, 4);
    voice_render(&v, l, r, 32); float s1 = v.step;
    for (int j = 0; j < 100; j++) voice_render(&v, l, r, 32);
    check("speed changes are smoothed (first block moves 5%, ~100 blocks settle)", fabs(s1 - 0.2f) < 1e-4 && fabs(v.step - 4) < 0.03);
    /* fader curve */
    check("fader curve: ends -4/+4, stop 61..66, exactly +1 at 92..99 and -1 at 28..35",
          fader_speed(0) == -4 && fader_speed(127) == 4 && fader_speed(61) == 0 && fader_speed(66) == 0 && fader_speed(60) < 0 && fader_speed(67) > 0
          && fader_speed(92) == 1 && fader_speed(99) == 1 && fader_speed(28) == -1 && fader_speed(35) == -1);
    int mono = 1; for (int f = 1; f < 128; f++) if (fader_speed(f) < fader_speed(f - 1)) mono = 0;
    check("fader curve never goes backwards", mono);
    /* a sweep for listening: the sine loop, fader 127 -> 0 over 4 s */
    int S = FS * 4; float *L = calloc(S, 4), *R = calloc(S, 4);
    voice_init(&v, sine, N, 1, FS, FS);
    for (int j = 0; j < S; j += 32) { voice_speed(&v, fader_speed(127 - (int)(127.0 * j / S))); voice_render(&v, L + j, R + j, 32); }
    char p[512]; snprintf(p, sizeof p, "%s/looper_sweep.wav", dir); wav(p, L, R, S); printf("  wrote %s\n", p);
    printf("FAILURES: %s\n", fails ? "some" : "none");
    return fails != 0;
}
