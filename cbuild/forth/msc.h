#ifndef MSC_H
#define MSC_H
#include "hw.h"
/* USB mass storage (Bulk-Only Transport, SCSI transparent command set) on endpoint 4 IN/OUT: the SD card as one
   read-only LUN. Polled from usb_poll() once the device is configured. */
#define MSC_EP 4
void msc_reset(void);              /* bus reset, SET_CONFIGURATION, Bulk-Only Mass Storage Reset */
void msc_poll(void);
extern volatile u32 msc_cmds, msc_blocks, msc_wblocks, msc_errors, msc_last_op;
extern volatile u32 msc_writable;   /* 0: report write-protected and refuse writes (the default) */
extern volatile u32 msc_offline;    /* 1: report the card removed (the R8 is writing to it) */
void msc_media_changed(void);
extern volatile u32 msc_in_use;     /* the host has it mounted: the R8 must not write to the card */
extern volatile u32 msc_present;    /* storage on (the host sees the card); off by default */
void msc_set_present(int on);
extern volatile u32 msc_log[32], msc_log_n;       /* next command: UNIT ATTENTION, medium may have changed */
#define MSC_WBUF 0xC0100000u      /* 64 KB write buffer in DDR (ours after the takeover) */
#define MSC_WCHUNK 128u           /* blocks per card write */
#endif
