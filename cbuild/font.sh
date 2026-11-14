# Sourced by the app build scripts (from cbuild/<app>/). Chooses the LCD font.
#
# By default the apps use our own font (forth/font.txt -> forth/font.h), and the binaries contain no Zoom data.
# FONT=stock builds with the stock R8 font instead, extracted from your own fw/R8SYS.BIN into forth/font_stock.h
# (git ignores it). A binary built that way contains Zoom's font data: keep it for your own use, don't share it.
FONTDEF=
if [ "${FONT:-}" = stock ]; then
    python3 -I ../../tools/mkfont.py --stock ../../fw/R8SYS.BIN ../forth/font_stock.h || exit 1
    FONTDEF=-DR8_STOCK_FONT
    echo "note: $(basename "$(pwd)") built with the stock firmware's font (FONT=stock): for your own use, don't share it" >&2
elif [ -n "${FONT:-}" ]; then
    echo "FONT=$FONT: unknown (use FONT=stock, or leave it unset for our own font)" >&2; exit 1
fi
