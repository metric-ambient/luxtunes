#!/usr/bin/env bash
# A small FFmpeg (ffmpeg + ffprobe) for the AppImage, built from source with
# only what LuxTunes uses, so a fresh install converts, rips and syncs without
# installing FFmpeg first:
#
#   bash packaging/appimage/build_ffmpeg.sh <output folder>
#   bash packaging/appimage/build_ffmpeg.sh <output folder> --windows
#
# Puts ffmpeg, ffprobe and their license and source notes in <output folder>.
# Needs a C compiler, make, nasm, pkg-config, curl, git and gpg (on Ubuntu:
# sudo apt install build-essential nasm pkg-config curl git gnupg zlib1g-dev).
# --windows makes ffmpeg.exe and ffprobe.exe for the Windows installer instead,
# built on Linux with MinGW (also: sudo apt install mingw-w64 libz-mingw-w64-dev).
#
# Every download is checked before it's used, and the build stops if one
# doesn't match:
#   FFmpeg  its release signature, made with FFmpeg's release key (pinned below);
#   LAME    its SHA-256 (pinned below);
#   x264    the exact git commit (a commit id is a checksum of its contents).
#
# UPDATING: change FFMPEG_VERSION (and, rarely, the others) and release.
# What's in it: the lists under "What LuxTunes uses". A new feature needing a
# format that isn't there (FFmpeg then says "Unknown encoder", "Unknown format"
# or "No such filter") means adding it to the right list.
set -euo pipefail

FFMPEG_VERSION="9.0.1"
FFMPEG_KEY="FCF986EA15E6E293A5644F10B4322F04D67658D8"   # FFmpeg release signing key
LAME_VERSION="3.100"
LAME_SHA256="ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"
X264_COMMIT="31e19f92f00c7003fa115047ce50978bc98c3a0d"   # x264's "stable" branch

out="${1:?usage: build_ffmpeg.sh <output folder> [--windows]}"
windows=""
if [ "${2:-}" = "--windows" ]; then
    windows=1
elif [ -n "${2:-}" ]; then
    echo "usage: build_ffmpeg.sh <output folder> [--windows]" >&2; exit 2
fi
mkdir -p "$out"
out="$(cd "$out" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
deps="$work/deps"
jobs="$(nproc 2>/dev/null || echo 2)"
stop() { echo "STOPPED: $*" >&2; exit 1; }

# Building for Windows: the same sources, compiled with MinGW, linked statically
# so ffmpeg.exe needs no DLLs beside it.
host=(); x264_host=(); ffmpeg_target=(); static_flags=""; exe=""; strip_tool="strip"
if [ -n "$windows" ]; then
    cross="x86_64-w64-mingw32"
    command -v "$cross-gcc" >/dev/null || stop "MinGW isn't installed (sudo apt install mingw-w64)."
    host=(--host="$cross")
    x264_host=(--host="$cross" --cross-prefix="$cross-")
    ffmpeg_target=(--arch=x86_64 --target-os=mingw32 --cross-prefix="$cross-" --pkg-config=pkg-config
                   --enable-w32threads)
    static_flags="-static"
    exe=".exe"
    strip_tool="$cross-strip"
fi

# ---- LAME (MP3 encoding) ----------------------------------------------------------------
echo "== LAME $LAME_VERSION"
curl -fsSL -o "$work/lame.tar.gz" \
    "https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz"
echo "$LAME_SHA256  $work/lame.tar.gz" | sha256sum -c --quiet - \
    || stop "LAME's download doesn't match its checksum: not using it."
tar -xzf "$work/lame.tar.gz" -C "$work"
(cd "$work/lame-$LAME_VERSION" &&
    ./configure "${host[@]}" --prefix="$deps" --enable-static --disable-shared --disable-frontend \
        --disable-decoder --disable-gtktest --with-pic >/dev/null &&
    make -j"$jobs" >/dev/null && make install >/dev/null)

# ---- x264 (H.264 video, for iPod versions of videos) -----------------------------------------
echo "== x264 ${X264_COMMIT:0:12}"
git clone -q --branch stable --single-branch https://code.videolan.org/videolan/x264.git "$work/x264"
git -C "$work/x264" -c advice.detachedHead=false checkout -q "$X264_COMMIT"
[ "$(git -C "$work/x264" rev-parse HEAD)" = "$X264_COMMIT" ] || stop "x264 isn't at the pinned commit."
(cd "$work/x264" &&
    ./configure "${x264_host[@]}" --prefix="$deps" --enable-static --enable-pic --disable-cli --disable-opencl \
        --disable-lavf --disable-swscale --disable-ffms --disable-gpac --disable-lsmash \
        --bit-depth=8 --chroma-format=420 >/dev/null &&
    make -j"$jobs" >/dev/null && make install >/dev/null)

# ---- FFmpeg ------------------------------------------------------------------------------
echo "== FFmpeg $FFMPEG_VERSION"
tarball="$work/ffmpeg-$FFMPEG_VERSION.tar.xz"
curl -fsSL -o "$tarball" "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
curl -fsSL -o "$tarball.asc" "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz.asc"
curl -fsSL -o "$work/ffmpeg-devel.asc" "https://ffmpeg.org/ffmpeg-devel.asc"
export GNUPGHOME="$work/gnupg"
mkdir -m 700 "$GNUPGHOME"
gpg -q --import "$work/ffmpeg-devel.asc" 2>/dev/null
gpg --batch --status-fd 1 --verify "$tarball.asc" "$tarball" 2>/dev/null \
    | grep -E '^\[GNUPG:\] VALIDSIG ' | grep -q "$FFMPEG_KEY" \
    || stop "FFmpeg's download isn't signed by FFmpeg's release key: not using it."
tar -xJf "$tarball" -C "$work"

# What LuxTunes uses (core/convert.py, core/encoders.py, core/cd.py, core/loudness.py,
# core/add_files.py, core/ipod/rockbox.py, core/ipod/photos.py).
decoders=(
    # audio people keep in a library
    mp3 mp3float mp2 aac aac_fixed alac flac vorbis opus ac3 eac3 wmav1 wmav2 ape wavpack
    pcm_s16le pcm_s16be pcm_s24le pcm_s24be pcm_s32le pcm_s32be pcm_f32le pcm_f32be
    pcm_f64le pcm_u8 pcm_s8 pcm_alaw pcm_mulaw adpcm_ima_wav
    # videos (for iPod versions) and pictures (covers, iPod photos)
    h264 hevc mpeg4 mpeg2video mpeg1video vp8 vp9 theora msmpeg4v3
    mjpeg png bmp gif tiff webp
)
encoders=(
    libmp3lame aac alac
    pcm_s16le pcm_s16be pcm_s24le pcm_s24be pcm_u8 pcm_s8      # WAV and AIFF (CD import)
    libx264 mjpeg                                               # iPod videos; JPEG covers
)
demuxers=(
    mp3 aac mov flac ogg wav w64 aiff caf ape wv asf matroska avi mpegps mpegts
    ac3 eac3 s16le s16be                                        # (s16le/s16be: CD audio)
    image2 image_jpeg_pipe image_png_pipe image_bmp_pipe image_gif_pipe image_tiff_pipe
    image_webp_pipe mjpeg
)
muxers=(ipod mp4 mov mp3 wav aiff image2 null)
parsers=(mpegaudio aac flac vorbis opus ac3 h264 hevc mpeg4video mpegvideo vp8 vp9 mjpeg png gif webp)
filters=(
    aresample aformat anull ebur128 highpass                    # audio
    scale format null fps setsar                                # pictures and video
    abuffer abuffersink buffer buffersink
)
bsfs=(aac_adtstoasc h264_mp4toannexb extract_extradata vp9_superframe_split)

join() { local IFS=,; echo "$*"; }
(cd "$work/ffmpeg-$FFMPEG_VERSION" &&
    PKG_CONFIG_PATH="$deps/lib/pkgconfig" ./configure "${ffmpeg_target[@]}" \
        --prefix="$work/ffmpeg-out" \
        --pkg-config-flags=--static \
        --extra-cflags="-I$deps/include" \
        --extra-ldflags="-L$deps/lib $static_flags" \
        --extra-libs="-lpthread -lm" \
        --enable-gpl --enable-libmp3lame --enable-libx264 --enable-zlib \
        --disable-autodetect --disable-everything --disable-network \
        --disable-doc --disable-ffplay --disable-debug --disable-shared --enable-static \
        --enable-ffmpeg --enable-ffprobe --enable-swscale --enable-swresample \
        --enable-protocol=file,pipe \
        --enable-decoder="$(join "${decoders[@]}")" \
        --enable-encoder="$(join "${encoders[@]}")" \
        --enable-demuxer="$(join "${demuxers[@]}")" \
        --enable-muxer="$(join "${muxers[@]}")" \
        --enable-parser="$(join "${parsers[@]}")" \
        --enable-filter="$(join "${filters[@]}")" \
        --enable-bsf="$(join "${bsfs[@]}")" >/dev/null &&
    make -j"$jobs" >/dev/null && make install >/dev/null)

install -m 755 "$work/ffmpeg-out/bin/ffmpeg$exe" "$work/ffmpeg-out/bin/ffprobe$exe" "$out/"
"$strip_tool" "$out/ffmpeg$exe" "$out/ffprobe$exe"
if [ -z "$windows" ]; then               # (a Windows build is tried on Windows, by windows/build.py)
    "$out/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q libmp3lame || stop "the new ffmpeg doesn't run."
fi
cp "$work/ffmpeg-$FFMPEG_VERSION/COPYING.GPLv2" "$out/LICENSE"
cat > "$out/SOURCES.txt" <<EOF
This FFmpeg was built for LuxTunes from these sources (GPL; the license is in LICENSE):
  FFmpeg $FFMPEG_VERSION  https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz
  LAME $LAME_VERSION      https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz
  x264 $X264_COMMIT  https://code.videolan.org/videolan/x264
How it was built: packaging/appimage/build_ffmpeg.sh in https://github.com/metric-ambient/luxtunes
EOF
du -h "$out/ffmpeg$exe" "$out/ffprobe$exe"
echo "Done: $out"
