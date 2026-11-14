/* Reading WAV files for utilities: the format, where the audio is, and samples as floats. */
#ifndef WAV_H
#define WAV_H
#include "r8.h"
#define WAV_CH_MAX 8
typedef struct {
    u32 rate, bits, ch, fmt;       /* fmt 1 = integer PCM, 3 = float (EXTENSIBLE is resolved to these) */
    u32 ba, doff, dlen, frames;    /* bytes per frame, data offset and length, frames */
} wav_info;
/* reads the header of path; 1 if it is a WAV we can do (PCM 8/16/24/32 or float 32, 1..8 channels, some audio) */
int wav_parse(const char *path, wav_info *w);
/* one sample (channel c of the frame at p) as a float in [-1, 1) */
float wav_sample(const wav_info *w, const u8 *p, u32 c);
/* a 44-byte canonical header for frames of (rate, bits, ch, fmt) */
void wav_header(u8 *h, u32 rate, u32 bits, u32 ch, u32 fmt, u32 frames);
#endif
