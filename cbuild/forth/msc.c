/* USB mass storage, Bulk-Only Transport (BOT 1.0) with SCSI transparent commands.
   One LUN: the SD card. Read-only unless msc_writable is set (Forth MSC-RW): then the MODE SENSE WP bit is clear and
   WRITE(10) data is gathered in a DDR buffer, up to MSC_WCHUNK blocks, and written with one multi-block command
   before the next chunk is accepted; the CSW goes out only after the card has programmed everything. Read-only:
   writes fail with DATA PROTECT / WRITE PROTECTED. The card is initialised on the first command that needs it.
   Data phases the device cannot fill are padded with zeros (BOT case Hi > Dn) and OUT data it does not want is
   accepted and dropped (case Ho > Dn), with the residue in the CSW; the only stall is for an invalid CBW, held until
   a Bulk-Only Mass Storage Reset as BOT 6.6.1 requires. Everything is polled and non-blocking except sd_read itself
   (about 0.3 ms per block). */
#include "msc.h"
#include "usbregs.h"
#include "sd.h"

volatile u32 msc_cmds, msc_blocks, msc_wblocks, msc_errors, msc_last_op, msc_writable;

enum { CBW, DATA_IN, DATA_OUT, CSW, STALLED };
static int state;
static u32 tag, xfer;              /* CBW tag and dCBWDataTransferLength */
static u32 in_valid, in_total, sent, received;   /* data phase: bytes of real data, bytes to send, progress */
static u32 csw_status, residue;
static u8 sense_key, sense_asc, sense_ascq;
static u8 resp[36];                /* small command responses */
static u32 rd_lba, rd_left;        /* READ(10): next block, blocks still to fetch */
static u32 blk[128];               /* one card block, word aligned for 32-bit FIFO writes */
static u32 blk_pos = 512;          /* bytes of blk already sent */
static int reading;                /* the data come from the card, not resp */
static int writing;                /* WRITE(10) data go to the card */
static int media_changed;          /* next command: UNIT ATTENTION (medium may have changed) */
static u32 wr_lba, wr_left, wr_chunk, wfill, written;   /* next block, blocks to go, this chunk, bytes buffered, done */
static u32 *const wbuf = (u32 *)MSC_WBUF;

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }
static void put32(u8 *p, u32 v) { p[0] = (u8)(v >> 24); p[1] = (u8)(v >> 16); p[2] = (u8)(v >> 8); p[3] = (u8)v; }
static void fail(u8 key, u8 asc, u8 ascq) { csw_status = 1; sense_key = key; sense_asc = asc; sense_ascq = ascq; msc_errors++; }

void msc_reset(void)
{
    state = CBW; reading = 0; writing = 0; blk_pos = 512; rd_left = 0;
    SET_INDEX(MSC_EP);
    CSRW(TXCSR, TX_MODE | TX_CLRDT | TX_FLUSH); CSRW(TXCSR, TX_MODE | TX_CLRDT);
    CSRW(RXCSR, RX_CLRDT | RX_FLUSH); CSRW(RXCSR, RX_CLRDT);
    SET_INDEX(0);
}

/* msc_offline: the card is busy on the R8's side (fat_create): report it as removed (as a card reader does), so the
   host unmounts it. Afterwards msc_media_changed() makes the next command fail once with UNIT ATTENTION, MEDIUM MAY
   HAVE CHANGED, so the host re-reads it from scratch and sees the new file. */
volatile u32 msc_offline;
volatile u32 msc_in_use;
volatile u32 msc_present;          /* storage on: the host sees the card. Off (the default): "no medium", so nothing
                                      mounts and the R8 can write to the card freely. A host eject turns it off. */
void msc_set_present(int on)
{
    if (on && !msc_present) media_changed = 1;                    /* host: UNIT ATTENTION, then re-reads the card */
    msc_present = on ? 1 : 0;
    if (!on) msc_in_use = 0;
}          /* the host has the card mounted (PREVENT MEDIUM REMOVAL, until an eject) */
volatile u32 msc_log[32], msc_log_n;   /* last 32 commands: op, byte 1, byte 4, sense key before */
void msc_media_changed(void) { media_changed = 1; }
static int media(void)              /* card ready? (initialise it on first use) */
{
    if (!msc_present) { fail(2, 0x3A, 0); return 0; }             /* storage off (or ejected): no medium */
    if (msc_offline) { fail(2, 0x3A, 0); return 0; }               /* NOT READY, MEDIUM NOT PRESENT */
    if (media_changed) { media_changed = 0; fail(6, 0x28, 0); return 0; }   /* UNIT ATTENTION */
    if (!sd_ready) sd_init();
    if (!sd_ready) { fail(2, 0x3A, 0); return 0; }  /* NOT READY, MEDIUM NOT PRESENT */
    return 1;
}

static u32 command(const u8 *cb)    /* run a SCSI command; returns the length of its data-in (resp or card) */
{
    u8 op = cb[0];
    msc_last_op = op;
    msc_log[msc_log_n++ & 31] = (u32)op << 24 | (u32)cb[1] << 16 | (u32)cb[4] << 8 | (sense_key & 0xF);   /* debug trace */
    if (op != 0x03) { sense_key = 0; sense_asc = 0; sense_ascq = 0; }
    switch (op) {
    case 0x00: media(); return 0;                                 /* TEST UNIT READY */
    case 0x03: {                                                  /* REQUEST SENSE: fixed format */
        for (int i = 0; i < 18; i++) resp[i] = 0;
        resp[0] = 0x70; resp[2] = sense_key; resp[7] = 10; resp[12] = sense_asc; resp[13] = sense_ascq;
        sense_key = 0; sense_asc = 0; sense_ascq = 0;
        return cb[4] < 18 ? cb[4] : 18;
    }
    case 0x12: {                                                  /* INQUIRY */
        if (cb[1] & 1) { fail(5, 0x24, 0); return 0; }            /* no vital product data pages */
        static const u8 inq[36] = { 0x00, 0x80, 0x04, 0x02, 31, 0, 0, 0,
            'R', '8', ' ', 'h', 'a', 'c', 'k', ' ',
            'R', '8', ' ', 'S', 'D', ' ', 'c', 'a', 'r', 'd', ' ', ' ', ' ', ' ', ' ', ' ',
            '0', '0', '0', '1' };
        for (int i = 0; i < 36; i++) resp[i] = inq[i];
        return cb[4] < 36 ? cb[4] : 36;
    }
    case 0x1A:                                                    /* MODE SENSE(6): header only, WP bit */
        resp[0] = 3; resp[1] = 0; resp[2] = msc_writable ? 0 : 0x80; resp[3] = 0;
        return cb[4] < 4 ? cb[4] : 4;
    case 0x5A: {                                                  /* MODE SENSE(10) */
        for (int i = 0; i < 8; i++) resp[i] = 0;
        resp[1] = 6; resp[3] = msc_writable ? 0 : 0x80;
        u32 n = (u32)cb[7] << 8 | cb[8];
        return n < 8 ? n : 8;
    }
    case 0x25:                                                    /* READ CAPACITY(10) */
        if (!media()) return 0;
        put32(resp, sd_blocks - 1); put32(resp + 4, 512);
        return 8;
    case 0x23: {                                                  /* READ FORMAT CAPACITIES */
        if (!media()) return 0;
        for (int i = 0; i < 12; i++) resp[i] = 0;
        resp[3] = 8; put32(resp + 4, sd_blocks); resp[8] = 2; resp[10] = 2;   /* formatted media, 512-byte blocks */
        u32 n = (u32)cb[7] << 8 | cb[8];
        return n < 12 ? n : 12;
    }
    case 0x28: {                                                  /* READ(10) */
        if (!media()) return 0;
        msc_in_use = 1;              /* the host is reading it (maybe mid-mount, before PREVENT): in use until ejected */
        u32 lba = be32(cb + 2), n = (u32)cb[7] << 8 | cb[8];
        if (lba >= sd_blocks || n > sd_blocks - lba) { fail(5, 0x21, 0); return 0; }   /* LBA OUT OF RANGE */
        rd_lba = lba; rd_left = n; blk_pos = 512; reading = 1;
        return n * 512;
    }
    case 0x1E: if (msc_present) msc_in_use = cb[4] & 1; return 0; /* PREVENT ALLOW MEDIUM REMOVAL: host mounted it */
    case 0x1B:                                                    /* START STOP UNIT with LoEj, no Start: ejected */
        if ((cb[4] & 3) == 2) msc_set_present(0);                 /* storage off (macOS then sends PREVENT: ignored) */
        return 0;
    case 0x35: case 0x2F: return 0;                               /* SYNC CACHE, VERIFY */
    case 0x2A: {                                                  /* WRITE(10) */
        if (!msc_writable) { fail(7, 0x27, 0); return 0; }
        if (!media()) return 0;
        u32 lba = be32(cb + 2), n = (u32)cb[7] << 8 | cb[8];
        if (lba >= sd_blocks || n > sd_blocks - lba) { fail(5, 0x21, 0); return 0; }
        wr_lba = lba; wr_left = n; wr_chunk = n < MSC_WCHUNK ? n : MSC_WCHUNK; wfill = 0; writing = n != 0;
        return n * 512;                                           /* bytes expected from the host */
    }
    case 0xAA: case 0x8A: case 0x2E: case 0x04:                   /* WRITE(12/16), WRITE AND VERIFY, FORMAT */
        fail(7, 0x27, 0); return 0;                               /* DATA PROTECT, WRITE PROTECTED */
    default:
        fail(5, 0x20, 0); return 0;                               /* ILLEGAL REQUEST, INVALID COMMAND OPERATION CODE */
    }
}

static void take_cbw(void)
{
    SET_INDEX(MSC_EP);
    u16 rc = R16(RXCSR);
    if (!(rc & RX_RXPKTRDY)) return;
    u32 n = R16(COUNT0) & 0x1FFF;
    u8 c[64];
    for (u32 i = 0; i < n && i < 64; i++) c[i] = R8(FIFO(MSC_EP));
    CSRW(RXCSR, 0);
    if (n != 31 || c[0] != 'U' || c[1] != 'S' || c[2] != 'B' || c[3] != 'C' || c[13] != 0 || (c[14] & 0x1F) < 1 || (c[14] & 0x1F) > 16) {
        CSRW(TXCSR, TX_MODE | TX_SENDSTALL); CSRW(RXCSR, RX_SENDSTALL);     /* invalid CBW: stall until reset */
        state = STALLED; msc_errors++; return;
    }
    tag = (u32)c[4] | (u32)c[5] << 8 | (u32)c[6] << 16 | (u32)c[7] << 24;
    xfer = (u32)c[8] | (u32)c[9] << 8 | (u32)c[10] << 16 | (u32)c[11] << 24;
    int dir_in = c[12] & 0x80;
    msc_cmds++; csw_status = 0; reading = 0; writing = 0; rd_left = 0;
    u32 len = command(c + 15);
    sent = 0; received = 0; written = 0;
    if (writing && (dir_in || xfer < len)) { writing = 0; csw_status = 2; }  /* direction or length mismatch */
    if (xfer == 0) {                                              /* no data phase */
        if (len) csw_status = 2;                                  /* device wanted to move data: phase error */
        writing = 0; residue = 0; state = CSW;
    } else if (dir_in) {
        if (len > xfer) { len = xfer; if (reading) csw_status = 2; }
        in_valid = len;
        in_total = (len < xfer && len % 64 == 0) ? xfer : len;    /* a short packet ends the transfer; else pad */
        state = DATA_IN;
    } else {
        if (!writing) {
            if (len && !csw_status) csw_status = 2;
            if (!csw_status) fail(5, 0x20, 0);                    /* nothing else takes data from the host */
        }
        reading = 0; state = DATA_OUT;
    }
    SET_INDEX(0);
}

static int send_packet(void)        /* next data-in packet; 0 if the FIFO is still busy */
{
    SET_INDEX(MSC_EP);
    if (R16(TXCSR) & TX_TXPKTRDY) return 0;
    u32 n = in_total - sent; if (n > 64) n = 64;
    if (reading && n == 64 && sent + 64 <= in_valid) {
        if (blk_pos == 512) {                                     /* fetch the next block */
            int e = sd_read(rd_lba, blk);
            if (e) {                                              /* card error: pad the rest, report it */
                fail(3, 0x11, 0); reading = 0; in_valid = sent; in_total = xfer;
            } else { rd_lba++; rd_left--; msc_blocks++; blk_pos = 0; }
            SET_INDEX(MSC_EP);
        }
        if (reading) {
            const u32 *w = blk + blk_pos / 4;
            for (int i = 0; i < 16; i++) R32(FIFO(MSC_EP)) = w[i];
            blk_pos += 64;
            goto commit;
        }
    }
    for (u32 i = 0; i < n; i++) {
        u32 k = sent + i;
        R8(FIFO(MSC_EP)) = (!reading && k < in_valid) ? resp[k] : 0;
    }
commit:
    CSRW(TXCSR, TX_MODE | TX_TXPKTRDY);
    sent += n;
    if (sent >= in_total) { residue = xfer - in_valid; reading = 0; state = CSW; }
    return 1;
}

static int out_packet(void)         /* data-out: into the write buffer, or dropped */
{
    SET_INDEX(MSC_EP);
    if (!(R16(RXCSR) & RX_RXPKTRDY)) return 0;
    u32 n = R16(COUNT0) & 0x1FFF;
    if (writing && n == 64 && wfill + 64 <= wr_chunk * 512) {
        u32 *d = wbuf + wfill / 4;
        for (int i = 0; i < 16; i++) d[i] = R32(FIFO(MSC_EP));
        wfill += 64;
    } else {
        for (u32 i = 0; i < n; i++) (void)R8(FIFO(MSC_EP));
        if (writing) { writing = 0; csw_status = 2; }             /* a short or odd packet inside write data */
    }
    CSRW(RXCSR, 0);                                               /* the host may send the next packet now */
    received += n;
    if (writing && wfill == wr_chunk * 512) {                     /* chunk complete: program it (host NAKed meanwhile) */
        int e = sd_write(wr_lba, wr_chunk, wbuf);
        if (e) { fail(3, 0x0C, 0); writing = 0; }                 /* MEDIUM ERROR, WRITE ERROR */
        else {
            wr_lba += wr_chunk; wr_left -= wr_chunk; written += wr_chunk * 512; msc_wblocks += wr_chunk;
            wfill = 0; wr_chunk = wr_left < MSC_WCHUNK ? wr_left : MSC_WCHUNK;
            if (!wr_left) writing = 0;
        }
    }
    if (received >= xfer || n < 64) {
        if (writing) { writing = 0; csw_status = 2; }             /* host stopped before all blocks arrived */
        residue = xfer - written; state = CSW;
    }
    return 1;
}

static int send_csw(void)
{
    SET_INDEX(MSC_EP);
    if (R16(TXCSR) & TX_TXPKTRDY) return 0;
    u8 c[13] = { 'U', 'S', 'B', 'S', (u8)tag, (u8)(tag >> 8), (u8)(tag >> 16), (u8)(tag >> 24),
                 (u8)residue, (u8)(residue >> 8), (u8)(residue >> 16), (u8)(residue >> 24), (u8)csw_status };
    for (int i = 0; i < 13; i++) R8(FIFO(MSC_EP)) = c[i];
    CSRW(TXCSR, TX_MODE | TX_TXPKTRDY);
    state = CBW;
    return 1;
}

void msc_poll(void)
{
    for (int k = 0; k < 32; k++) {  /* keep going while there is progress, then let the rest of the loop run */
        int progress = 0;
        switch (state) {
        case CBW: { int s0 = state; take_cbw(); progress = state != s0; break; }
        case DATA_IN: progress = send_packet(); break;
        case DATA_OUT: progress = out_packet(); break;
        case CSW: progress = send_csw(); break;
        default: break;
        }
        if (!progress) break;
    }
    SET_INDEX(0);
}
