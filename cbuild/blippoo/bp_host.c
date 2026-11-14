/* Render the Blippoo DSP (bp.c) on a computer: bp_host OUT.wav SECONDS [param=value ...] [sweep=param:from:to]
   Values are 0..1 (fader positions); params by number or name (OSC_A ... SPARE_8). Prints level statistics per
   second and the comparator pulse rate, and fails on NaN or a stuck output. 16-bit mono 44.1 kHz. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "bp.h"

static const char *names[BP_NPARAM] = { "OSC_A", "OSC_B", "SWEEP_A", "SWEEP_B", "RUNG_A", "RUNG_B", "WARP", "COLOUR",
    "PEAK_1", "PEAK_2", "RUNG_PK", "SPREAD", "EXT", "PING", "SPARE_7", "SPARE_8" };
static int param(const char *s)
{
    for (int i = 0; i < BP_NPARAM; i++) if (!strcmp(s, names[i])) return i;
    return atoi(s);
}
static void put32(FILE *f, unsigned v) { fputc(v, f); fputc(v >> 8, f); fputc(v >> 16, f); fputc(v >> 24, f); }
static void put16(FILE *f, unsigned v) { fputc(v, f); fputc(v >> 8, f); }

int main(int argc, char **argv)
{
    if (argc < 3) { fprintf(stderr, "usage: bp_host OUT.wav SECONDS [param=value ...] [sweep=param:from:to]\n"); return 2; }
    const int rate = 44100, n = (int)(atof(argv[2]) * rate);
    static bp_t b; bp_init(&b, rate);
    int sp = -1; float s0 = 0, s1 = 0, impulse = 0;
    for (int a = 3; a < argc; a++) {
        char k[32]; float v, v2;
        if (sscanf(argv[a], "sweep=%31[^:]:%f:%f", k, &v, &v2) == 3) { sp = param(k); s0 = v; s1 = v2; continue; }
        if (sscanf(argv[a], "impulse=%f", &v) == 1) { impulse = v; continue; }   /* one external-input impulse at t = 0 */
        if (sscanf(argv[a], "%31[^=]=%f", k, &v) == 2) { int i = param(k); bp_set(&b, i, v); b.p[i] = b.target[i]; }
    }
    short *pcm = malloc(sizeof(short) * n);
    float buf[64];
    double sum = 0; float pk = 0; int bad = 0; unsigned cross0 = 0;
    for (int i = 0; i < n; i += 64) {
        int m = n - i < 64 ? n - i : 64;
        if (sp >= 0) bp_set(&b, sp, s0 + (s1 - s0) * (float)i / (float)n);
        float inb[64] = { 0 };
        if (i == 0) inb[0] = impulse;
        bp_render(&b, impulse ? inb : 0, buf, m);
        for (int j = 0; j < m; j++) {
            float y = buf[j];
            if (!(y == y)) { bad = 1; y = 0; }
            sum += y * y; if (fabsf(y) > pk) pk = fabsf(y);
            pcm[i + j] = (short)lrintf(y * 32000.0f);
        }
        if ((i + m) % rate < 64 || i + m == n) {
            double rms = sqrt(sum / rate);
            printf("t=%5.1fs rms %6.1f dBFS peak %6.1f dBFS pulses/s %u\n", (double)(i + m) / rate,
                   20 * log10(rms + 1e-12), 20 * log10(pk + 1e-12), b.crossings - cross0);
            sum = 0; pk = 0; cross0 = b.crossings;
        }
    }
    FILE *f = fopen(argv[1], "wb");
    fwrite("RIFF", 1, 4, f); put32(f, 36 + 2 * n); fwrite("WAVEfmt ", 1, 8, f); put32(f, 16);
    put16(f, 1); put16(f, 1); put32(f, rate); put32(f, rate * 2); put16(f, 2); put16(f, 16);
    fwrite("data", 1, 4, f); put32(f, 2 * n); fwrite(pcm, 2, n, f); fclose(f);
    if (bad) { printf("NaN in the output\n"); return 1; }
    return 0;
}
