/* Panel link on UART2: 166,666 baud 8N1, opened as stock does; start-up replay; MIDI-like parser.
   Panel->DSP: status byte >= 0x80, then data. C3..CA faders 1-8, CB MASTER (value 0..127). Keys: <id> 03 (press).
   Snapshot: DSP sends 9E 01 00 F7 (part of the stock start-up); the panel answers E0 + 22 data bytes, of which
   bytes 12..20 (1-based) are faders 1-8 and MASTER (decoded on hardware 2026-11-08).
   The 4 keys under the screen are A3..A6 (left to right; 03 press, 04 release); the scroll wheel is D0 01 per
   clockwise click and D0 7F per counter-clockwise click (captured on hardware 2026-11-08). */
#include "panel.h"
volatile u8 fader[NFADERS];
u32 fader_known;                                             /* bit per fader: position reported at least once */
u32 sel_down;                                                /* bit 0..3: key A3..A6 held */
int wheel_delta;                                             /* clicks since last taken (+ = clockwise) */
u32 fader_changed, key_pressed;
u32 trk_pressed;
volatile u8 panel_keyq[16]; volatile u32 panel_keyq_w;         /* every key press (status 0x80..0xA7), for menus */                                              /* bit k: track k+1 status key pressed (edge) */
volatile u32 panel_c1;                                      /* supply value (C1 hh ll, 14 bits); 0 = not seen yet */
volatile u32 pad_down;                                       /* bit per pad 0..31: held */
volatile u8 pad_vel[32];                                     /* velocity of the last hit (kept after release) */
volatile u16 pad_q[32]; volatile u32 pad_q_w;                /* every pad message: pad << 8 | velocity (0 = release) */
void (*panel_raw)(u8 b);                                     /* if set: every received byte, before parsing */
static u8 status, nd, d0, d1, dk, c1_hi;

static void tx(u8 b) { while (!(REG(UART2 + 0x14) & 0x20)) ; REG(UART2) = b; }
static void txs(const u8 *p, int n) { while (n--) tx(*p++); }

void panel_init(void)
{
    static const u32 init[][2] = { {0x30, 0}, {0x20, 67}, {0x24, 0}, {0x34, 0}, {0x08, 0}, {0x08, 1}, {0x08, 0x0F},
                                   {0x04, 0}, {0x10, 0}, {0x0C, 3}, {0x30, 0x6001} };
    for (unsigned i = 0; i < sizeof init / sizeof init[0]; i++) REG(UART2 + init[i][0]) = init[i][1];
    static const u8 start[] = { 0x84,1,1,1,0xF7, 0xA0,1,1,2,0xF7, 0x9E,1,0,0xF7, 0x81,1,0,0xF7, 0x83,1,0,0xF7 };
    static const u8 later[] = { 0x8A,1,1,2,0xF7, 0x8B,1,1,0,0xF7 };
    txs(start, sizeof start);
    u32 t0 = TSCL; while (TSCL - t0 < CPU_HZ / 5) panel_poll();       /* 200 ms, still draining input */
    txs(later, sizeof later);
}
void panel_send(const u8 *p, int n) { txs(p, n); }
void pad_lights(u32 v)                                       /* A6 01 02 hi lo F7: bit k = pad k+1 lit */
{
    u8 m[6] = { 0xA6, 1, 2, (u8)(v >> 4 & 15), (u8)(v & 15), 0xF7 };
    txs(m, 6);
}
void panel_request(void)                                     /* ask for a snapshot (answer arrives in ~10 ms) */
{
    static const u8 req[] = { 0x9E, 1, 0, 0xF7 };
    txs(req, sizeof req);
}
void panel_poll(void)
{
    while (REG(UART2 + 0x14) & 1) {                          /* LSR.DR */
        u8 b = (u8)REG(UART2);
        if (panel_raw) panel_raw(b);
        if (b & 0x80) { status = b; nd = 0; dk = 0; continue; }
        if (status == 0xE0) {                                /* snapshot */
            int k = dk++ - 11;
            if (k >= 0 && k < NFADERS) { fader[k] = b; fader_changed |= 1u << k; fader_known |= 1u << k; }   /* always apply */
            if (dk == 21) c1_hi = b; else if (dk == 22) panel_c1 = (u32)c1_hi << 7 | b;   /* bytes 21-22: C1 */
            continue;
        }
        if (nd == 0) { d0 = b; nd = 1; }
        else if (nd == 1) { d1 = b; nd = 2; }
        else nd = 3;
        if (status == 0xC1 && nd == 2) { panel_c1 = (u32)d0 << 7 | b; nd = 0; continue; }   /* C1 hh ll, sent on change */
        if (status == 0xF0) {                                /* pad: F0 hi lo vv, one bit per pad (below) */
            if (nd == 3) {
                u32 mask = ((u32)d0 << 4 | d1) & 0xFF;       /* pads 1-4 = 00 01/02/04/08, 5-8 = 01/02/04/08 00 */
                for (u32 pad = 1; pad <= 8; pad++) if (mask & (1u << (pad - 1))) {
                    if (b) { pad_down |= 1u << pad; pad_vel[pad] = b; } else pad_down &= ~(1u << pad);
                    pad_q[pad_q_w++ & 31] = (u16)(pad << 8 | b);
                }
                nd = 0;                                      /* (running status, should the panel use it) */
            }
            continue;
        }
        if (status >= 0x80 && status <= 0xA7 && nd == 1 && d0 == 3) panel_keyq[panel_keyq_w++ & 15] = status;
        if (status >= 0xC3 && status <= 0xCB && nd == 1) {
            int f = status - 0xC3;
            if (fader[f] != b) { fader[f] = b; fader_changed |= 1u << f; }
            fader_known |= 1u << f;
            nd = 0;                                          /* running status: next data byte is a new value */
        } else if (status == 0xA0 && nd == 1 && d0 == 3) {
            key_pressed |= 1;                                /* STOP press */
        } else if (status == 0xA2 && nd == 1 && d0 == 3) {
            key_pressed |= 2;                                /* REC press */
        } else if (status == 0x82 && nd == 1 && d0 == 3) {
            key_pressed |= 4;                                /* USB press */
        } else if (status >= 0x8F && status <= 0x92 && nd == 1 && d0 == 3) {
            trk_pressed |= 1u << (status - 0x8F);            /* track 1-4 status keys */
        } else if (status >= 0x99 && status <= 0x9C && nd == 1 && d0 == 3) {
            trk_pressed |= 1u << (status - 0x99 + 4);        /* track 5-8 status keys */
        } else if (status >= 0xA3 && status <= 0xA6 && nd == 1) {   /* the 4 keys under the screen */
            u32 bit = 1u << (status - 0xA3);
            if (d0 == 3) sel_down |= bit; else if (d0 == 4) sel_down &= ~bit;
        } else if (status == 0xD0 && nd == 1) {              /* scroll wheel: 1 = one click clockwise, 7F = back */
            wheel_delta += b < 64 ? (int)b : (int)b - 128;
            nd = 0;
        }
    }
}
