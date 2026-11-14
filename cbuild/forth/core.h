#ifndef CORE_H
#define CORE_H
/* R8 Forth core: a Forth prompt on a USB serial port (CDC-ACM; /dev/cu.usbmodem* on a Mac) with the LCD console,
   USB storage, the power switch, battery shutdown, and the general words (SD, storage, panel, LCD, audio pass-through,
   audio clock, RELOAD, BYE). Apps build on it: the plain Forth app (forth/main.c) adds nothing, the sampler
   (sampler/main.c) adds the looper and starts it at boot. Add ad hoc words to an app with forth_word(). */
#include "hw.h"

/* What an app adds to the core. Every hook may be 0. */
struct core_app {
    const char *name;                   /* first word of the status row, e.g. "FORTH" */
    const char *title;                  /* the terminal banner, e.g. "R8 Forth" */
    int  (*busy)(void);                 /* nonzero while the app owns the screen, the panel and the audio */
    void (*poll)(void);                 /* called from forth_poll(), often */
    void (*screen)(void);               /* redraw the app's screen (every 100 ms while busy) */
    void (*stop)(void);                 /* stop audio etc. before power-off, RELOAD or BYE */
    void (*rate_changed)(void);         /* RATE changed the output sample rate */
    void (*storage_released)(void);     /* the computer let go of the card: files may have changed */
};

void core_init(const struct core_app *app);   /* hardware, LCD, panel link, USB, Forth and the core words */
void core_run(void);                          /* the prompt loop; never returns */

void core_puts(const char *s);          /* text to the terminal (and the LCD console) */
void core_service(void);                /* USB and the power switch: call while a long job runs */
void core_console_back(void);           /* redraw the console after the app gave the screen back */
void core_glyphs(int row, int col, const char *s);   /* text straight into the LCD frame buffer at pixel column col */
void core_panel_up(void);               /* open the panel link (start-up sequence) if not done yet */
int  core_panel_is_up(void);
void core_faders_sync(void);            /* read every fader position from the panel now (fader[] in panel.h) */
#endif
