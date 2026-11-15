#!/bin/sh
# Downloads the Mesa sources (MIT) that tools/test_vp9_message.py compiles as the reference for the VP9 message.
# Pinned to the commit NootedRed/VP9Message.hpp was ported from. Output: build/vp9/mesa (ignored by git).
set -eu
COMMIT=767fbf7c6492f64549c90e9a080ab5417895a9ec
OUT="$(cd "$(dirname "$0")/.." && pwd)/build/vp9/mesa"
mkdir -p "$OUT"
for f in src/amd/common/ac_vcn_dec.c src/amd/common/ac_vcn_dec.h src/amd/common/ac_video_dec.h \
         src/amd/common/ac_vcn_vp9_default.h src/gallium/include/pipe/p_video_state.h \
         src/gallium/drivers/radeonsi/mm/si_video_dec.c src/gallium/frontends/va/picture_vp9.c src/util/vl_vlc.h; do
    curl -sfL -o "$OUT/$(basename "$f")" "https://gitlab.freedesktop.org/mesa/mesa/-/raw/$COMMIT/$f"
done
echo "$COMMIT" > "$OUT/COMMIT"
echo "Mesa $COMMIT -> $OUT"
