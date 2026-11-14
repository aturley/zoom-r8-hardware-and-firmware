#ifndef LCD_H
#define LCD_H
#include "hw.h"
extern u8 fb[1024];                 /* band*128 + column, bit 0 = top row of the band */
void lcd_init(void);
void lcd_mark(int band);            /* band needs sending */
int  lcd_step(void);                /* send one byte of pending work; returns 0 when idle */
void lcd_flush(void);
void lcd_text(int band, int col, const char *s);
extern const u8 LCD_CONTRAST_STOCK[8];  /* stock CONTRAST 1..8 -> 81 vv value (default 5 = 0x12) */
void lcd_contrast(u32 v);               /* send 81 vv (0..63) after finishing any band in progress */
#endif
