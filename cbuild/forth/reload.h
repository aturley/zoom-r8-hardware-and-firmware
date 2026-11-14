#ifndef RELOAD_H
#define RELOAD_H
#include "hw.h"
const char *reload_check(const char *path, void (*poll)(void));   /* read + verify an app ("APP.BIN", "R8APPS/X.BIN"); 0 or why not */
/* The app chooser leaves the path of the app it started here, so the app can find (and RELOAD) itself. */
#define LAUNCH_INFO 0xC03FFF00u
#define LAUNCH_MAGIC 0x504C3852u                  /* 'R8LP' */
void reload_set_launched(const char *path);
const char *reload_launched(void);             /* the path, or 0 if not started by the chooser */
void reload_go(void);                          /* copy it over the running app and jump in (never returns) */
#endif
