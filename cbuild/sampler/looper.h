#ifndef LOOPER_H
#define LOOPER_H
/* Four-loop variable-speed player. Loops: the first four WAVs (by name) in ZOOM_R8/LOOP on the card.
   Audio runs from the timer 1 interrupt (INT15, 8 kHz), which keeps the McASP write FIFO topped up and reads the
   panel: faders 1/3/5/7 set the speed of loops 1-4 (middle = stopped, top +4x, bottom -4x), faders 2/4/6/8 their
   level, MASTER the output level; STOP restarts every loop from its beginning. */
#include "hw.h"
#define LP_VOICES 4
extern int lp_running;
extern const char *lp_err[LP_VOICES];   /* last load error per track, or 0 */
void lp_out_rate(void);                  /* call after the output rate changes */
extern volatile u32 lp_ints, lp_underruns, lp_isr_max, lp_isr_cycles, lp_frames;
extern char lp_names[LP_VOICES][40];
extern u32 lp_len[LP_VOICES], lp_rate[LP_VOICES], lp_ch[LP_VOICES];
/* Load the loops (msg() reports progress), set up the audio and the panel, start the interrupt. Returns the number
   of loops loaded, or -1 if the clock or the card is missing (msg says why). */
int  lp_start(void (*msg)(const char *s), void (*poll)(void));
void lp_stop(void);                     /* fade out, interrupt off, McASP transmit back to reset */
void lp_fader(int f, int v);            /* as if panel fader f (0..8; 8 = MASTER) moved to v (0..127) */
int  lp_fader_get(int f);
float lp_speed(int voice);
void lp_restart_loops(void);            /* what STOP does */
void lp_panel_up(void);                /* open the panel link (start-up sequence) if not done yet */
int  lp_panel_is_up(void);
void lp_faders_sync(void);              /* read every fader position from the panel now */
/* Choosing files: hold a key under the screen (A3..A6 = tracks 1-4) and turn the wheel; release to load it. */
void lp_service(void);                  /* main loop: performs the loads (call often while running) */
int  lp_track_state(int i);             /* 0 empty, 1 playing, 2 selecting (key held), 3 loading */
int  lp_track_pick(int i);              /* file index being picked (state 2) or loaded (3) */
int  lp_track_file(int i);              /* file index playing */
int  lp_nfiles(void);
const char *lp_file_name(int k);
void lp_load_request(int i, int k);     /* load file k into track i now, as the keys would */
/* Input (McASP receive through the read FIFO): monitor level and recording into a track's slot. */
extern volatile u32 lp_in_frames, lp_in_overruns, lp_in_short, lp_in_drops, lp_rec_n, lp_rec_max, lp_tx_fixes, lp_rx_fixes;
extern volatile s32 lp_in_peak[2];
extern volatile int lp_rec_track;
void lp_monitor(int v);                 /* 0..127 (fader curve), 0 = off */
int  lp_rec_start(int track);           /* 0, or <0: not running / bad track / already recording */
int  lp_rec_start_mix(int track);       /* the same, recording the mix of the other tracks (pre-MASTER) */
extern volatile int lp_rec_mix;
int  lp_rec_stop(void);                 /* frames recorded (the track then loops them), or -1 */
/* Save track i's recording as ZOOM_R8/LOOP/RECnnnnn.WAV (next free number); 0 or an error text. The card is
   reported removed over USB while writing, then changed. Refused while the Mac has it writable (MSC-RW). */
const char *lp_save(int i);
int lp_rescan(void);                    /* re-read ZOOM_R8/LOOP into the pick list; count, or -1 */
#endif
