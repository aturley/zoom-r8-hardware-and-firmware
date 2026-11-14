#include "voice.h"
#define Q32 4294967296.0f

void voice_init(voice_t *v, const short *data, unsigned frames, int ch, unsigned file_rate, unsigned out_rate)
{
    v->data = data; v->frames = frames; v->ch = ch; v->pos = 0;
    v->rate = (float)file_rate / (float)out_rate;
    v->step = v->target = 0.0f; v->smooth = 0.05f; v->gain = 1.0f;
}
void voice_speed(voice_t *v, float speed) { v->target = speed * v->rate; }
void voice_out_rate(voice_t *v, unsigned file_rate, unsigned out_rate)
{
    float r = (float)file_rate / (float)out_rate, k = r / v->rate;
    v->target *= k; v->step *= k; v->rate = r;          /* speed = target / rate stays the same */
}

/* Catmull-Rom / 4-point Hermite through y0..y3, t in [0,1) between y1 and y2 */
static inline float herm(float y0, float y1, float y2, float y3, float t)
{
    float c1 = 0.5f * (y2 - y0);
    float c2 = y0 - 2.5f * y1 + 2.0f * y2 - 0.5f * y3;
    float c3 = 0.5f * (y3 - y0) + 1.5f * (y1 - y2);
    return ((c3 * t + c2) * t + c1) * t + y1;
}

void voice_render(voice_t *v, float *l, float *r, int n)
{
    v->step += (v->target - v->step) * v->smooth;
    const unsigned N = v->frames;
    const long long len = (long long)N << 32;
    long long pos = v->pos;
    /* the (smoothed) step for this block, as Q32 frames */
    long long st = (long long)(v->step * Q32);
    const short *d = v->data;
    const float k = v->gain * (1.0f / 32768.0f);
    for (int j = 0; j < n; j++) {
        unsigned i = (unsigned)(pos >> 32);
        float t = (float)(unsigned)pos * (1.0f / Q32);
        unsigned i0, i2, i3;
        if (i >= 1 && i + 2 < N) { i0 = i - 1; i2 = i + 1; i3 = i + 2; }
        else { i0 = i ? i - 1 : N - 1; i2 = i + 1 < N ? i + 1 : i + 1 - N; i3 = i + 2 < N ? i + 2 : i + 2 - N; }
        if (v->ch == 2) {
            float a = herm(d[2 * i0], d[2 * i], d[2 * i2], d[2 * i3], t);
            float b = herm(d[2 * i0 + 1], d[2 * i + 1], d[2 * i2 + 1], d[2 * i3 + 1], t);
            l[j] += a * k; r[j] += b * k;
        } else {
            float a = herm(d[i0], d[i], d[i2], d[i3], t) * k;
            l[j] += a; r[j] += a;
        }
        pos += st;
        if (pos >= len) pos -= len; else if (pos < 0) pos += len;
    }
    v->pos = pos;
}

/* Fader curve, 0..127. Flat zones so a fader can land exactly on a speed without a detent:
   stopped in the middle (61..66), exactly +1 at 92..99 and exactly -1 at 28..35; between the zones the speed
   moves linearly; the ends reach +-4. (An easy table to reshape once it has been tried.) */
float fader_speed(int f)
{
    if (f < 0) f = 0; else if (f > 127) f = 127;
    int s = 1; if (f < 64) { s = -1; f = 127 - f; }   /* mirror: 127..64 */
    float x;
    if (f <= 66) x = 0.0f;                              /* 61..66: stop */
    else if (f < 92) x = (float)(f - 66) / 26.0f;       /* 0..1 */
    else if (f <= 99) x = 1.0f;                         /* 92..99: exactly 1 */
    else x = 1.0f + 3.0f * (float)(f - 99) / 28.0f;     /* 1..4 at 127 */
    return s * x;
}
