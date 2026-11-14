#ifndef USBREGS_H
#define USBREGS_H
/* USB0 register map (TI wrapper + Mentor MUSB), shared by usb.c and msc.c */
#include "hw.h"
#define USB0      0x01E00000u
#define CTRLR     (USB0 + 0x04)
#define INTSRCR   (USB0 + 0x20)
#define INTCLRR   (USB0 + 0x28)
#define INTMSKCLR (USB0 + 0x34)
#define R8(o)  (*(volatile u8 *)(USB0 + 0x400 + (o)))
#define R16(o) (*(volatile u16 *)(USB0 + 0x400 + (o)))
/* The compiler may put two volatile stores in one execute packet; they then reach the controller in the same
   cycle in no defined order. Index switches and CSR writes (which commit or release FIFO data) get a packet of
   their own: an asm statement is a scheduling barrier. */
#define BAR() __asm(" NOP")
#define SET_INDEX(n) do { BAR(); R8(INDEX) = (u8)(n); BAR(); } while (0)
#define CSRW(o, v) do { BAR(); R16(o) = (u16)(v); BAR(); } while (0)
#define FADDR 0x00
#define POWER 0x01
#define INTRTXE 0x06
#define INTRRXE 0x08
#define INTRUSBE 0x0B
#define INDEX 0x0E
#define TXMAXP 0x10
#define CSR0 0x12                   /* TXCSR for endpoints 1-4 */
#define TXCSR 0x12
#define RXMAXP 0x14
#define RXCSR 0x16
#define COUNT0 0x18                 /* RXCOUNT for endpoints 1-4 */
#define FIFO(n) (0x20 + 4 * (n))
#define DEVCTL 0x60
#define TXFIFOSZ 0x62
#define RXFIFOSZ 0x63
#define TXFIFOADDR 0x64
#define RXFIFOADDR 0x66
#define CFGCHIP2 (SYSCFG0 + 0x184)
/* CSR0 (peripheral) */
#define C0_RXPKTRDY 0x01
#define C0_TXPKTRDY 0x02
#define C0_SENTSTALL 0x04
#define C0_DATAEND 0x08
#define C0_SETUPEND 0x10
#define C0_SENDSTALL 0x20
#define C0_SERV_RX 0x40
#define C0_SERV_SETUPEND 0x80
/* TXCSR / RXCSR (peripheral) */
#define TX_TXPKTRDY 0x0001
#define TX_FIFONOTEMPTY 0x0002
#define TX_FLUSH 0x0008
#define TX_SENTSTALL 0x0020
#define TX_CLRDT 0x0040
#define TX_MODE 0x2000
#define RX_RXPKTRDY 0x0001
#define RX_FLUSH 0x0010
#define RX_SENTSTALL 0x0040
#define RX_CLRDT 0x0080
/* wrapper interrupt source bits */
#define I_TX(n) (1u << (n))
#define I_RX(n) (1u << (8 + (n)))
#define I_RESET (1u << 18)
#define R32(o) (*(volatile u32 *)(USB0 + 0x400 + (o)))
#define TX_SENDSTALL 0x0010
#define RX_SENDSTALL 0x0020
#endif
