/* C6748 / Zoom R8 hardware used by the standalone firmware */
#ifndef HW_H
#define HW_H
typedef unsigned int u32; typedef int s32; typedef unsigned short u16; typedef short s16; typedef unsigned char u8;
#define REG(a) (*(volatile u32 *)(a))

#define PSC0      0x01C10000u
#define PSC1      0x01E27000u
#define SYSCFG0   0x01C14000u
#define GPIO      0x01E26000u
#define SPI1      0x01F0E000u
#define UART2     0x01D0D000u
#define MCASP0    0x01D00000u

/* GPIO banks are in pairs: bank 2/3 at +0x38, bank 8 at +0xB0 (DIR, OUT, SET, CLR, IN at +0,+4,+8,+C,+10) */
#define GPIO_B23  (GPIO + 0x38)
#define GPIO_B45  (GPIO + 0x60)
#define GPIO_B67  (GPIO + 0x88)
#define GPIO_B8   (GPIO + 0xB0)
#define G_DIR 0x00
#define G_OUT 0x04
#define G_SET 0x08
#define G_CLR 0x0C
#define G_IN  0x10

extern cregister volatile unsigned int TSCL;
extern cregister volatile unsigned int CSR;
extern cregister volatile unsigned int IER;
extern cregister volatile unsigned int ICR;

#define CPU_HZ 360000000u
void delay_us(u32 us);
void psc_enable(u32 psc, int lpsc);
void pinmux_stock(void);
void r8_restart(void);              /* reboot into the stock firmware (watchdog reset); never returns */
#endif
