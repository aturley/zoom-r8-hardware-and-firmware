/* USB0 device: full-speed composite of CDC-ACM (one serial port) and mass storage (msc.c), PIO, polled.
   Bring-up follows the stock firmware's USB start routine (0xC076BA30): controller soft reset, PHY reset pulse,
   no OTG override, PHY and OTG power on, VBUS/session detect, 24 MHz reference (CFGCHIP2 = 0x0932), wait for the
   PHY clock. Events come from the TI wrapper's raw interrupt source register (INTSRCR, write-1-to-clear INTCLRR),
   as the stock firmware and Linux's da8xx glue use it; endpoint registers are reached through INDEX.
   Endpoints: 0 control, 1 bulk IN (data to host), 2 bulk OUT (data from host), 3 interrupt IN (CDC notify),
   4 bulk IN + OUT (mass storage). The CDC pair is grouped by an interface association descriptor. */
#include "usb.h"
#include "usbregs.h"
#include "msc.h"

volatile u32 usb_strategy, usb_ep0_events, usb_phy_ok, usb_resets, usb_setups, usb_stalls, usb_configured, usb_dtr, usb_addr;
volatile u8 usb_last_req[8];
static u32 t_strategy;            /* when the current connect strategy started */

static const u8 dev_desc[18] = { 18, 1, 0x00, 0x02, 0xEF, 0x02, 0x01, 64, 0x09, 0x12, 0x01, 0x00, 0x00, 0x02, 1, 2, 3, 1 };
                                                            /* class EF/02/01: composite with IAD; bcdDevice 2.00 */
static const u8 cfg_desc[98] = {
    9, 2, 98, 0, 3, 1, 0, 0xC0, 50,                         /* configuration: 3 interfaces, self-powered */
    8, 0x0B, 0, 2, 0x02, 0x02, 0x01, 0,                     /* IAD: interfaces 0-1 are one CDC ACM function */
    9, 4, 0, 0, 1, 0x02, 0x02, 0x01, 0,                     /* interface 0: CDC communications, ACM */
    5, 0x24, 0x00, 0x10, 0x01,                              /* header, CDC 1.10 */
    5, 0x24, 0x01, 0x00, 0x01,                              /* call management: data on interface 1 */
    4, 0x24, 0x02, 0x02,                                    /* ACM: line coding + control line state */
    5, 0x24, 0x06, 0x00, 0x01,                              /* union: master 0, slave 1 */
    7, 5, 0x83, 0x03, 16, 0, 16,                            /* EP3 IN interrupt, 16 bytes, 16 ms */
    9, 4, 1, 0, 2, 0x0A, 0x00, 0x00, 0,                     /* interface 1: CDC data */
    7, 5, 0x02, 0x02, 64, 0, 0,                             /* EP2 OUT bulk */
    7, 5, 0x81, 0x02, 64, 0, 0,                             /* EP1 IN bulk */
    9, 4, 2, 0, 2, 0x08, 0x06, 0x50, 0,                     /* interface 2: mass storage, SCSI, bulk-only */
    7, 5, 0x80 | MSC_EP, 0x02, 64, 0, 0,                    /* EP4 IN bulk */
    7, 5, MSC_EP, 0x02, 64, 0, 0,                           /* EP4 OUT bulk */
};
static const char *const strings[] = { 0, "R8 hack", "R8 Forth", "R8F1" };
static u8 line_coding[7] = { 0x00, 0xC2, 0x01, 0x00, 0, 0, 8 };   /* 115200 8N1 (ignored: it is USB) */

/* ---- rings: rx (host -> us), tx (us -> host) */
#define RXN 1024
#define TXN 4096
static u8 rxb[RXN], txb[TXN];
static u32 rx_r, rx_w, tx_r, tx_w;
int usb_getc(void) { if (rx_r == rx_w) return -1; int c = rxb[rx_r % RXN]; rx_r++; return c; }
int usb_rx_take(u8 c)               /* is c waiting? then drop everything up to and including it */
{
    for (u32 i = rx_r; i != rx_w; i++) if (rxb[i % RXN] == c) { rx_r = i + 1; return 1; }
    return 0;
}
int usb_tx_free(void) { return TXN - (int)(tx_w - tx_r); }
int usb_putc(u8 c) { if (tx_w - tx_r >= TXN) return 0; txb[tx_w % TXN] = c; tx_w++; return 1; }

/* ---- EP0 */
enum { IDLE, IN_DATA, OUT_DATA, STATUS };
static int ep0_state;
static const u8 *ep0_src; static u32 ep0_left;
static u8 ep0_buf[64];
static u8 pending_addr; static int addr_pending;
static int out_req;                 /* class request whose data stage we are receiving */
static int last_tx_full;            /* last bulk IN packet was 64 bytes: may owe a zero-length packet */

static void fifo_write(int ep, const u8 *p, u32 n) { while (n--) R8(FIFO(ep)) = *p++; }
static void fifo_read(int ep, u8 *p, u32 n) { while (n--) *p++ = R8(FIFO(ep)); }

static void ep0_send_chunk(void)    /* load the next IN packet; DATAEND with the last */
{
    u32 n = ep0_left > 64 ? 64 : ep0_left;
    fifo_write(0, ep0_src, n);
    ep0_src += n; ep0_left -= n;
    int last = n < 64 || ep0_left == 0;
    CSRW(CSR0, C0_TXPKTRDY | (last ? C0_DATAEND : 0));
    ep0_state = last ? STATUS : IN_DATA;
}
static void ep0_in(const u8 *p, u32 n, u32 wlen)
{
    ep0_src = p; ep0_left = n < wlen ? n : wlen;
    CSRW(CSR0, C0_SERV_RX);
    ep0_send_chunk();
}
static void ep0_ok(void) { CSRW(CSR0, C0_SERV_RX | C0_DATAEND); ep0_state = STATUS; }
static void ep0_stall(void) { CSRW(CSR0, C0_SERV_RX | C0_SENDSTALL); ep0_state = IDLE; usb_stalls++; }

static void config_endpoints(void)
{
    SET_INDEX(1); R16(TXMAXP) = 64; CSRW(TXCSR, TX_MODE | TX_CLRDT | TX_FLUSH); CSRW(TXCSR, TX_MODE | TX_CLRDT);
    SET_INDEX(3); R16(TXMAXP) = 16; CSRW(TXCSR, TX_MODE | TX_CLRDT | TX_FLUSH); CSRW(TXCSR, TX_MODE | TX_CLRDT);
    SET_INDEX(2); R16(RXMAXP) = 64; CSRW(RXCSR, RX_CLRDT | RX_FLUSH); CSRW(RXCSR, RX_CLRDT);
    SET_INDEX(MSC_EP); R16(TXMAXP) = 64; R16(RXMAXP) = 64;
    SET_INDEX(0);
    msc_reset();
    last_tx_full = 0;
}

static void setup_packet(void)
{
    u8 s[8];
    SET_INDEX(0);
    if ((R16(COUNT0) & 0x7F) != 8) { ep0_stall(); return; }
    fifo_read(0, s, 8);
    for (int i = 0; i < 8; i++) usb_last_req[i] = s[i];
    usb_setups++;
    u8 type = s[0], req = s[1];
    u16 value = (u16)(s[2] | s[3] << 8), index = (u16)(s[4] | s[5] << 8), wlen = (u16)(s[6] | s[7] << 8);
    (void)index;
    if ((type & 0x60) == 0x00) {                                   /* standard */
        switch (req) {
        case 6: {                                                  /* GET_DESCRIPTOR */
            u8 t = (u8)(value >> 8), i = (u8)value;
            if (t == 1) { ep0_in(dev_desc, sizeof dev_desc, wlen); return; }
            if (t == 2 && i == 0) { ep0_in(cfg_desc, sizeof cfg_desc, wlen); return; }
            if (t == 3 && i < 4) {
                u32 n = 2;
                if (i == 0) { ep0_buf[2] = 0x09; ep0_buf[3] = 0x04; n = 4; }      /* English (US) */
                else for (const char *p = strings[i]; *p && n < 62; p++) { ep0_buf[n++] = (u8)*p; ep0_buf[n++] = 0; }
                ep0_buf[0] = (u8)n; ep0_buf[1] = 3;
                ep0_in(ep0_buf, n, wlen); return;
            }
            ep0_stall(); return;                                   /* incl. device qualifier: full-speed only */
        }
        case 5: pending_addr = (u8)(value & 0x7F); addr_pending = 1; ep0_ok(); return;     /* SET_ADDRESS */
        case 9:                                                    /* SET_CONFIGURATION */
            if (value > 1) { ep0_stall(); return; }
            usb_configured = value; if (value) config_endpoints();
            ep0_ok(); return;
        case 8: ep0_buf[0] = (u8)usb_configured; ep0_in(ep0_buf, 1, wlen); return;      /* GET_CONFIGURATION */
        case 0: ep0_buf[0] = (type & 0x1F) == 0 ? 1 : 0; ep0_buf[1] = 0; ep0_in(ep0_buf, 2, wlen); return;   /* GET_STATUS */
        case 10: ep0_buf[0] = 0; ep0_in(ep0_buf, 1, wlen); return;                     /* GET_INTERFACE */
        case 11: ep0_ok(); return;                                 /* SET_INTERFACE (alt 0 only) */
        case 1: case 3:                                            /* CLEAR_FEATURE / SET_FEATURE */
            if ((type & 0x1F) == 2 && value == 0 && req == 1) {    /* clear ENDPOINT_HALT: reset the toggle */
                int ep = index & 0x0F;
                SET_INDEX(ep);
                if (index & 0x80) CSRW(TXCSR, TX_MODE | TX_CLRDT); else CSRW(RXCSR, RX_CLRDT);   /* also ends a stall */
                SET_INDEX(0);
            }
            ep0_ok(); return;
        }
    } else if ((type & 0x60) == 0x20) {                            /* class (CDC) */
        switch (req) {
        case 0x20: if (wlen == 7) { out_req = 0x20; CSRW(CSR0, C0_SERV_RX); ep0_state = OUT_DATA; return; } break;  /* SET_LINE_CODING */
        case 0x21: ep0_in(line_coding, 7, wlen); return;           /* GET_LINE_CODING */
        case 0x22: usb_dtr = value & 1; ep0_ok(); return;          /* SET_CONTROL_LINE_STATE */
        case 0x23: ep0_ok(); return;                               /* SEND_BREAK */
        case 0xFE: ep0_buf[0] = 0; ep0_in(ep0_buf, 1, wlen); return;  /* mass storage GET_MAX_LUN: one LUN */
        case 0xFF: if (usb_configured) msc_reset(); ep0_ok(); return;  /* Bulk-Only Mass Storage Reset */
        }
    }
    ep0_stall();
}

static void ep0_event(int flagged)  /* flagged: the wrapper reported an EP0 event */
{
    SET_INDEX(0);
    u16 c = R16(CSR0);
    if (flagged) usb_ep0_events++;
    if (c & C0_SENTSTALL) { CSRW(CSR0, 0); ep0_state = IDLE; }                 /* clear SENTSTALL */
    if (c & C0_SETUPEND) { CSRW(CSR0, C0_SERV_SETUPEND); ep0_state = IDLE; }   /* host aborted the transfer */
    if (ep0_state == STATUS && (flagged || (c & C0_RXPKTRDY))) {  /* status stage finished (or a new SETUP came) */
        if (addr_pending) { R8(FADDR) = pending_addr; usb_addr = pending_addr; addr_pending = 0; }
        ep0_state = IDLE;
    }
    if (ep0_state == IN_DATA && !(c & C0_TXPKTRDY)) { ep0_send_chunk(); return; }
    if (ep0_state == OUT_DATA && (c & C0_RXPKTRDY)) {
        u32 n = R16(COUNT0) & 0x7F;
        u8 d[64]; fifo_read(0, d, n);
        if (out_req == 0x20 && n == 7) for (int i = 0; i < 7; i++) line_coding[i] = d[i];
        ep0_ok(); return;
    }
    if (ep0_state == IDLE && (R16(CSR0) & C0_RXPKTRDY)) setup_packet();
}

static void bus_reset(void)
{
    usb_resets++; usb_configured = 0; usb_dtr = 0; usb_addr = 0;
    ep0_state = IDLE; addr_pending = 0;
    R8(FADDR) = 0;
}

static void bulk(void)
{
    if (!usb_configured) return;
    SET_INDEX(2);                                                 /* OUT: host -> rx ring */
    u16 rc = R16(RXCSR);
    if (rc & RX_SENTSTALL) CSRW(RXCSR, 0);
    if (rc & RX_RXPKTRDY) {
        u32 n = R16(COUNT0) & 0x7F;
        if (RXN - (rx_w - rx_r) >= n) {                           /* otherwise leave it: the host is NAKed */
            for (u32 i = 0; i < n; i++) { rxb[rx_w % RXN] = R8(FIFO(2)); rx_w++; }
            CSRW(RXCSR, 0);                                        /* clear RXPKTRDY */
        }
    }
    SET_INDEX(1);                                                 /* IN: tx ring -> host */
    u16 tc = R16(TXCSR);
    if (tc & TX_SENTSTALL) CSRW(TXCSR, TX_MODE);
    if (!(tc & TX_TXPKTRDY)) {
        u32 n = tx_w - tx_r;
        if (n || last_tx_full) {
            if (n > 64) n = 64;
            for (u32 i = 0; i < n; i++) { R8(FIFO(1)) = txb[tx_r % TXN]; tx_r++; }
            CSRW(TXCSR, TX_MODE | TX_TXPKTRDY);
            last_tx_full = n == 64;
        }
    }
    SET_INDEX(0);
    msc_poll();
}

static void apply_strategy(void);
void usb_disconnect(void) { R8(POWER) = 0; }
void usb_reconnect(void)
{
    R8(POWER) = 0; usb_configured = 0; usb_dtr = 0;
    delay_us(500000);
    apply_strategy();                                              /* pull-up back on; the host resets us */
}
u32 usb_diag(int which) { return which == 0 ? R8(POWER) : which == 1 ? R8(DEVCTL) : which == 2 ? REG(CFGCHIP2) : REG(INTSRCR); }
static void psc_state(int lpsc, u32 st)  /* PSC1 module state: 0 sync reset, 2 disable (clock off), 3 enable */
{
    u32 t0 = TSCL;
    while ((REG(PSC1 + 0x128) & 1) && TSCL - t0 < CPU_HZ / 100) ;
    REG(PSC1 + 0xA00 + 4 * lpsc) = (REG(PSC1 + 0xA00 + 4 * lpsc) & ~0x1Fu) | st;
    REG(PSC1 + 0x120) = 1;                                         /* PTCMD: power domain 0 */
    while ((REG(PSC1 + 0x128) & 1) && TSCL - t0 < CPU_HZ / 100) ;
    while ((REG(PSC1 + 0x800 + 4 * lpsc) & 0x1F) != st && TSCL - t0 < CPU_HZ / 100) ;
}
void usb_init(void)
{
    /* the stock start routine (0xC076BA30), step by step */
    psc_state(1, 0); psc_state(1, 3);                              /* USB0 module reset, then on */
    REG(INTMSKCLR) = 0x01FF1F1Fu;                                  /* polled: nothing to the CPU */
    REG(CTRLR) |= 1;                                               /* soft reset */
    for (u32 t0 = TSCL; (REG(CTRLR) & 1) && TSCL - t0 < CPU_HZ / 100; ) ;
    psc_state(1, 2);                                               /* module clock off while the PHY is set up */
    u32 c = REG(CFGCHIP2);
    REG(CFGCHIP2) = c | (1u << 15); delay_us(10); c &= ~(1u << 15); REG(CFGCHIP2) = c;   /* PHY reset pulse */
    c &= ~(3u << 13); REG(CFGCHIP2) = c;                           /* no OTG mode override */
    c &= ~(1u << 10); REG(CFGCHIP2) = c;                           /* PHY power on */
    psc_state(1, 3);                                               /* module clock on */
    c &= ~(1u << 9); c |= (1u << 5) | (1u << 4); REG(CFGCHIP2) = c;   /* OTG power on, session end + VBUS detect */
    c = (c & ~0xFu) | 2; REG(CFGCHIP2) = c;                        /* reference clock 24 MHz */
    usb_phy_ok = 0;
    for (u32 t0 = TSCL; TSCL - t0 < CPU_HZ / 10; ) if (REG(CFGCHIP2) & (1u << 17)) { usb_phy_ok = 1; break; }
    REG(CTRLR) &= ~(1u << 3);                                      /* wrapper (PDR) interrupt mode, as stock */
    R8(POWER) = 0;                                                 /* disconnected, full speed only */
    R8(FADDR) = 0;
    R16(INTRTXE) = 0x1F; R16(INTRRXE) = 0x1E; R8(INTRUSBE) = 0xFF; /* as stock: events reach INTSRCR */
    /* FIFO RAM (8-byte units; EP0 has the first 64 bytes): EP1 TX 64 @64, EP2 RX 64 @128, EP3 TX 16 @192, EP4 below */
    SET_INDEX(1); R8(TXFIFOSZ) = 3; R16(TXFIFOADDR) = 8;
    SET_INDEX(2); R8(RXFIFOSZ) = 3; R16(RXFIFOADDR) = 16;
    SET_INDEX(3); R8(TXFIFOSZ) = 1; R16(TXFIFOADDR) = 24;
    SET_INDEX(MSC_EP); R8(TXFIFOSZ) = 3; R16(TXFIFOADDR) = 32; R8(RXFIFOSZ) = 3; R16(RXFIFOADDR) = 40;   /* 64 @256, 64 @320 */
    SET_INDEX(0);
    REG(INTCLRR) = 0xFFFFFFFFu;
    bus_reset(); usb_resets = 0;
    R8(POWER) = 0x40;                                              /* SOFTCONN: D+ pull-up on */
    usb_strategy = 2; apply_strategy();                            /* start with C */
}

/* Connect strategies, tried in turn until a host resets us (the R8 does not report VBUS to the controller:
   DEVCTL reads 0x80 with the cable in): A as stock, B request a session (DEVCTL.SESSION, as Linux does when VBUS
   is not valid), C force device mode in the PHY (CFGCHIP2 OTG mode 2 = VBUS valid, B-device) plus the session.
   C is what works on the R8 (confirmed 2026-11-07), so it is tried first; the others stay as fallbacks. */
static void apply_strategy(void)
{
    R8(POWER) = 0;                                                 /* drop the pull-up so the host sees a new device */
    delay_us(20000);
    u32 c = REG(CFGCHIP2) & ~(3u << 13);
    if (usb_strategy >= 2) c |= 2u << 13;
    REG(CFGCHIP2) = c;
    delay_us(1000);
    if (usb_strategy >= 1) R8(DEVCTL) = (u8)(R8(DEVCTL) | 1);
    delay_us(1000);
    R8(POWER) = 0x40;
    t_strategy = TSCL;
}
void usb_poll(void)
{
    if (!usb_resets && TSCL - t_strategy > 3u * CPU_HZ) {            /* nobody has reset us: try the next way */
        usb_strategy = (usb_strategy + 1) % 3;
        apply_strategy();
    }
    u32 s = REG(INTSRCR);
    if (s) REG(INTCLRR) = s;
    if (s & I_RESET) bus_reset();
    ep0_event((s & I_TX(0)) != 0);                                 /* every poll: also catches SETUPs without a flag */
    bulk();
}
