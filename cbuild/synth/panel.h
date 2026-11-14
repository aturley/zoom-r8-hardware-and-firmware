#ifndef PANEL_H
#define PANEL_H
#include "hw.h"
#define NFADERS 9                   /* faders 1-8, MASTER */
extern volatile u8 fader[NFADERS];  /* 0..127 */
extern u32 fader_changed;           /* bit per fader, set by panel_poll */
extern u32 trk_pressed;
extern volatile u8 panel_keyq[16];  /* key presses (panel status 0x80..0xA7), oldest first from a reader's own index */
extern volatile u32 panel_keyq_w;             /* bit k: the status key above fader k+1 pressed (edge) */
extern u32 key_pressed;             /* bit 0: STOP, bit 1: REC, bit 2: USB pressed (edges) */
extern u32 fader_known;             /* bit per fader: its position has been reported */
extern u32 sel_down;                /* bit 0..3: the keys under the screen (A3..A6) held */
extern int wheel_delta;             /* scroll wheel clicks not yet taken (+ = clockwise) */
/* Pads (hardware 2026-11-13): F0 hi lo vv, (hi << 4 | lo) has one bit per pad: pad 1-4 = 00 01, 00 02, 00 04, 00 08;
   pad 5-8 = 01 00, 02 00, 04 00, 08 00. vv = velocity (soft about 8, hard about 0x68), 0 = release. One message
   per pad, also when two are held. Nothing is sent while a pad is held (no pressure). Pads are numbered 1-8 here. */
extern volatile u32 panel_c1;                 /* supply value from C1 messages and the snapshot (0 = not seen) */
extern volatile u32 pad_down;                 /* bit n: pad n (1-8) held */
extern volatile u8 pad_vel[32];               /* last hit's velocity per pad */
extern volatile u16 pad_q[32];                /* every pad message, pad << 8 | velocity, from a reader's own index */
extern volatile u32 pad_q_w;
extern void (*panel_raw)(u8 b);               /* optional: called with every byte received from the panel */
void panel_init(void);
void panel_poll(void);
void panel_send(const u8 *p, int n);  /* raw bytes to the panel processor */
void panel_request(void);
void pad_lights(u32 v);              /* pad lights: bit k = pad k+1 (as stock: A6 01 02 hi lo F7) */           /* ask the panel for every fader position (parsed by panel_poll) */
#endif
