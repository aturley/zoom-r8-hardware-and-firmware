#ifndef USB_H
#define USB_H
#include "hw.h"
/* USB0 (Mentor MUSB + TI wrapper) as a full-speed CDC-ACM serial device, polled (no interrupts). */
void usb_init(void);
void usb_poll(void);
int  usb_getc(void);                /* -1 if nothing received */
int  usb_putc(u8 c);                /* 0 if the transmit buffer is full */
int  usb_tx_free(void);
int  usb_rx_take(u8 c);             /* Ctrl-C check while a word runs */
void usb_disconnect(void);
void usb_reconnect(void);           /* drop off the bus for 0.5 s so the host enumerates us again */
u32  usb_diag(int which);            /* 0 POWER, 1 DEVCTL, 2 CFGCHIP2, 3 INTSRCR */
/* state for the status line */
extern volatile u32 usb_strategy, usb_ep0_events, usb_phy_ok, usb_resets, usb_setups, usb_stalls, usb_configured, usb_dtr, usb_addr;
extern volatile u8 usb_last_req[8];
#endif
