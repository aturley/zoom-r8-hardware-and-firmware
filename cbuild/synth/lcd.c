/* ST7565 on SPI1 (CS GP2[14], A0 GP8[9], RESET GP8[8]); non-blocking band sender for use in the audio loop */
#include "lcd.h"
#include "audio.h"
#define CS_BIT  (1u << 14)          /* GP2[14]: bank 2 = low half of the 2/3 register pair */
#define A0_BIT  (1u << 9)           /* GP8[9] */
#define RST_BIT (1u << 8)           /* GP8[8] */
u8 fb[1024];
static u8 dirty;                    /* bit per band */
static int cur_band = -1, cur_pos;  /* -3..-1: page/col commands, 0..127 data */

static void spi_byte(u8 b)            /* as the proven standalone driver: SPIDAT1, wait SPIBUF.RXEMPTY = 0 */
{
    REG(SPI1 + 0x3C) = b;
    while (REG(SPI1 + 0x40) & 0x80000000u) ;
}
static void cmd(u8 c)
{
    REG(GPIO_B23 + G_CLR) = CS_BIT; REG(GPIO_B8 + G_CLR) = A0_BIT;
    spi_byte(c);
    REG(GPIO_B8 + G_SET) = A0_BIT; REG(GPIO_B23 + G_SET) = CS_BIT;
}
static void ms(u32 n) { delay_us(n * 1000u); }

void lcd_init(void)
{
    REG(GPIO_B23 + G_SET) = CS_BIT; REG(GPIO_B8 + G_SET) = A0_BIT | RST_BIT;
    REG(GPIO_B23 + G_DIR) &= ~CS_BIT; REG(GPIO_B8 + G_DIR) &= ~(A0_BIT | RST_BIT);
    static const u32 spi_init[][2] = { {0x00, 0}, {0x00, 1}, {0x04, 3}, {0x14, 0x0E01}, {0x08, 0}, {0x0C, 0},
        {0x48, 0x03030000}, {0x4C, 0}, {0x50, (17u << 8) | (1u << 17) | 8}, {0x3C, 0}, {0x04, 0x01000003} };
    for (unsigned i = 0; i < sizeof spi_init / sizeof spi_init[0]; i++) REG(SPI1 + spi_init[i][0]) = spi_init[i][1];
    static const u8 init[] = { 0xAE, 0xA2, 0xA0, 0xC0, 0x26, 0x81, 0x0D, 0xA6, 0xA4, 0x40, 0xB0, 0x10, 0x00, 0x2C };
    static const u8 boost[] = { 0x2D, 0x2F, 0x2C, 0x2D, 0x2F, 0x2C, 0x2D, 0x2F, 0x2C, 0x2D, 0x2F };
    ms(2);
    for (int k = 0; k < 2; k++) {
        REG(GPIO_B8 + G_CLR) = RST_BIT; ms(30); REG(GPIO_B8 + G_SET) = RST_BIT; ms(100);
    }
    ms(100);
    for (unsigned i = 0; i < sizeof init; i++) cmd(init[i]);
    for (unsigned i = 0; i < sizeof boost; i++) { ms(10); cmd(boost[i]); }
    ms(10); cmd(0x81); cmd(0x0D); cmd(0xAF);
}
void lcd_mark(int band) { dirty |= (u8)(1u << band); }
int lcd_step(void)
{
    if (cur_band < 0) {
        if (!dirty) return 0;
        for (cur_band = 0; !(dirty & (1u << cur_band)); cur_band++) ;
        dirty &= (u8)~(1u << cur_band); cur_pos = -3;
    }
    if (cur_pos == -3) cmd((u8)(0xB0 + cur_band));
    else if (cur_pos == -2) cmd(0x00);
    else if (cur_pos == -1) { cmd(0x10); REG(GPIO_B23 + G_CLR) = CS_BIT; }
    else spi_byte(fb[cur_band * 128 + cur_pos]);
    if (++cur_pos == 128) { REG(GPIO_B23 + G_SET) = CS_BIT; cur_band = -1; }
    return 1;
}
void lcd_flush(void) { while (lcd_step()) ; }

/* Contrast: ST7565 electronic volume, 81 vv (vv = 0..63). Finishes any band in progress first, so the
   command never lands in the middle of display data. Stock's CONTRAST 1..8 = LCD_CONTRAST_STOCK[n-1]. */
const u8 LCD_CONTRAST_STOCK[8] = { 13, 14, 15, 16, 18, 20, 22, 25 };   /* stock 1.17 table at 0xC0713BE0 */
void lcd_contrast(u32 v)
{
    lcd_flush();
    cmd(0x81); cmd((u8)(v & 0x3F));
}

static const struct { char c; u8 col[5]; } FONT[] = {     /* 5x7, column-major, bit 0 = top */
    {'A',{0x7E,0x09,0x09,0x09,0x7E}}, {'C',{0x3E,0x41,0x41,0x41,0x22}}, {'D',{0x7F,0x41,0x41,0x41,0x3E}},
    {'E',{0x7F,0x49,0x49,0x49,0x41}}, {'H',{0x7F,0x08,0x08,0x08,0x7F}}, {'I',{0x00,0x41,0x7F,0x41,0x00}},
    {'M',{0x7F,0x02,0x0C,0x02,0x7F}}, {'O',{0x3E,0x41,0x41,0x41,0x3E}}, {'R',{0x7F,0x09,0x19,0x29,0x46}},
    {'S',{0x46,0x49,0x49,0x49,0x31}}, {'T',{0x01,0x01,0x7F,0x01,0x01}}, {'U',{0x3F,0x40,0x40,0x40,0x3F}},
    {'F',{0x7F,0x09,0x09,0x09,0x01}}, {'K',{0x7F,0x08,0x14,0x22,0x41}}, {'L',{0x7F,0x40,0x40,0x40,0x40}},
    {'N',{0x7F,0x04,0x08,0x10,0x7F}}, {'X',{0x63,0x14,0x08,0x14,0x63}},
    {'0',{0x3E,0x51,0x49,0x45,0x3E}}, {'1',{0x00,0x42,0x7F,0x40,0x00}}, {'2',{0x42,0x61,0x51,0x49,0x46}},
    {'3',{0x21,0x41,0x45,0x4B,0x31}}, {'4',{0x18,0x14,0x12,0x7F,0x10}}, {'5',{0x27,0x45,0x45,0x45,0x39}},
    {'6',{0x3C,0x4A,0x49,0x49,0x30}}, {'7',{0x01,0x71,0x09,0x05,0x03}}, {'8',{0x36,0x49,0x49,0x49,0x36}},
    {'9',{0x06,0x49,0x49,0x29,0x1E}}, {' ',{0,0,0,0,0}},
};
void lcd_text(int band, int col, const char *s)
{
    for (; *s; s++, col += 6, audio_pump())
        for (unsigned f = 0; f < sizeof FONT / sizeof FONT[0]; f++)
            if (FONT[f].c == *s) {
                for (int i = 0; i < 5; i++) if (col + i < 128) fb[band * 128 + col + i] = FONT[f].col[i];
                if (col + 5 < 128) fb[band * 128 + col + 5] = 0;
            }
    lcd_mark(band);
}
