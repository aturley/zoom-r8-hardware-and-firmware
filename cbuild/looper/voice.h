#ifndef VOICE_H
#define VOICE_H
/* One loop voice: plays a 16-bit PCM loop (mono or interleaved stereo, any sample rate) at a variable speed,
   forwards or backwards, wrapping around the loop. 4-point Hermite interpolation, speed changes smoothed.
   Plain C (float) so it builds for the C674x and on the host for tests. */
typedef struct {
    const short *data;          /* frames * ch samples */
    unsigned frames;            /* loop length in frames (>= 4) */
    int ch;                     /* 1 or 2 */
    long long pos;              /* playback position, Q32 frames, kept in [0, frames << 32) */
    float rate;                 /* file rate / output rate */
    float step, target;         /* frames per output frame: current (smoothed) and wanted */
    float smooth;               /* per-block smoothing factor (0..1]; 1 = jump at once */
    float gain;
} voice_t;

void  voice_init(voice_t *v, const short *data, unsigned frames, int ch, unsigned file_rate, unsigned out_rate);
void  voice_speed(voice_t *v, float speed);           /* 1 = normal, -1 = reverse, 0 = stopped */
void  voice_out_rate(voice_t *v, unsigned file_rate, unsigned out_rate);   /* new output rate, same speed and pitch */
/* Add n frames into l[] and r[] (full scale = 1.0). Speed smoothing advances once per call, so call it with
   short blocks (16-64 frames). */
void  voice_render(voice_t *v, float *l, float *r, int n);
float fader_speed(int fader127);                       /* the fader curve: 0 -> -4, middle -> 0, 127 -> +4 */
#endif
