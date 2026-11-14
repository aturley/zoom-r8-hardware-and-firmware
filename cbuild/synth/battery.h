#ifndef BATTERY_H
#define BATTERY_H
/* Low-battery detection for apps, as the stock firmware does it (writeup section 5, "Power"): only while running on
   batteries (GP6[1] low, debounced), comparing the panel's supply value C1 with the thresholds stock uses for the
   BATTERY setting (0 = Alkaline, 1 = Ni-MH; read from stock's memory at start-up).
   LOW:   C1 below stock's level-1 point (Alkaline 213, Ni-MH 233; about 4.6 / 4.96 V): warn, finish writing files.
   EMPTY: C1 below stock's level-0 point (Alkaline 203, Ni-MH 228; about 4.38 / 4.87 V) for batt_empty_us in a row:
          switch off (power hold GP7[0] low). */
#include "hw.h"
enum { BATT_OK, BATT_LOW, BATT_EMPTY };
void battery_init(void);            /* call first in main(): reads stock's BATTERY setting before DDR is reused */
int  battery_poll(void);            /* call often from the main loop; returns BATT_OK / BATT_LOW / BATT_EMPTY */
extern int batt_type;               /* 0 Alkaline, 1 Ni-MH (an unknown value is treated as Ni-MH: earlier cut-off) */
extern int batt_on_usb;             /* debounced GP6[1] */
extern u32 batt_empty_us;           /* how long below the empty point before EMPTY (default 4 s, as stock) */
#endif
