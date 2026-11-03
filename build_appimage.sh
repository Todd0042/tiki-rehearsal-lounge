#!/bin/bash
set -e

# ==============================================================================
# Tiki Rehearsal Lounge - Automated Linux AppImage Builder
# Builds a universal, standalone Linux AppImage inside a clean Debian container
# for maximum backward GLIBC compatibility (Ubuntu, Debian, Mint, Fedora, Arch)
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_TMP="/tmp/tiki_rehearsal_build_$(date +%s)"
OUT_DIR="${REPO_ROOT}/dist"
APPDIR="/tmp/TikiRehearsalLounge.AppDir"
VERSION="${STUDIO_VERSION:-1.0.0}"
OUT_APPIMAGE="${OUT_DIR}/TikiRehearsalLounge-v${VERSION}-x86_64.AppImage"

echo "=== [1/5] Setting up build directories ==="
rm -rf "$APPDIR" 2>/dev/null || true
mkdir -p "$OUT_DIR"
mkdir -p "$BUILD_TMP"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/icons/hicolor/512x512/apps"
mkdir -p "$APPDIR/usr/share/applications"

echo "=== [2/5] Compiling standalone binary inside Debian container ==="
cat << 'EOF' > "${BUILD_TMP}/docker_build.sh"
#!/bin/bash
set -e
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev python3-tk tk-dev libpython3.11 binutils curl file ca-certificates gcc libc6-dev

python3 -m venv /tmp/venv
. /tmp/venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r /src/requirements.txt pyinstaller

mkdir -p /out /tmp/pyi_work /tmp/pyi_spec
pyinstaller --clean -y --onedir \
    --name TikiRehearsalLounge \
    --add-data "/src/static:static" \
    --distpath /out \
    --workpath /tmp/pyi_work \
    --specpath /tmp/pyi_spec \
    --hidden-import audio \
    --hidden-import audio.calibration \
    --hidden-import audio.player \
    --hidden-import audio.recorder \
    --hidden-import audio.tone_generator \
    --hidden-import audio.vocal_analyzer \
    --hidden-import ai \
    --hidden-import ai.offline_coach \
    --hidden-import db \
    --hidden-import db.bridge \
    --hidden-import db.progression_db \
    --hidden-import ui \
    --hidden-import ui.studio_window \
    --hidden-import config \
    --hidden-import downloader \
    --collect-all yt_dlp \
    --collect-all numpy \
    --collect-all scipy \
    --collect-all PIL \
    --collect-all tkinter \
    --hidden-import PIL._tkinter_finder \
    --hidden-import tkinter \
    --hidden-import tkinter.messagebox \
    --hidden-import tkinter.filedialog \
    --hidden-import tkinter.ttk \
    /src/main.py
EOF

chmod +x "${BUILD_TMP}/docker_build.sh"
docker run --rm \
    -v "${REPO_ROOT}:/src:ro" \
    -v "${BUILD_TMP}:/out" \
    -v "${BUILD_TMP}/docker_build.sh:/build.sh:ro" \
    debian:bookworm-slim /bin/bash /build.sh

echo "=== [3/5] Assembling AppDir ==="
cp -r "${BUILD_TMP}/TikiRehearsalLounge"/* "$APPDIR/usr/bin/"

# Stage Standalone llama.cpp Linux engine into AppDir
echo "=== Staging Linux llama.cpp Server into AppDir ==="
LLAMA_TAR="/tmp/llama_linux_x64.tar.gz"
if [ ! -f "$LLAMA_TAR" ]; then
    curl -sL "https://github.com/ggml-org/llama.cpp/releases/download/b12078/llama-b12078-bin-ubuntu-x64.tar.gz" -o "$LLAMA_TAR"
fi
tar -xzf "$LLAMA_TAR" -C "/tmp"
cp -v /tmp/llama-b12078/llama-server "$APPDIR/usr/bin/"
cp -v /tmp/llama-b12078/*.so* "$APPDIR/usr/bin/"
chmod +x "$APPDIR/usr/bin/llama-server"

if [ -f "${REPO_ROOT}/static/tiki-icon.png" ]; then
    cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/tiki-icon.png"
    cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/usr/bin/tiki-icon.png"
    mkdir -p "$APPDIR/usr/bin/static"
    cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/usr/bin/static/tiki-icon.png"
    cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/usr/share/icons/hicolor/512x512/apps/tiki-icon.png"
fi

cat << 'EOF' > "$APPDIR/tiki-rehearsal-lounge.desktop"
[Desktop Entry]
Name=Tiki Rehearsal Lounge
GenericName=Vocal Analysis & Rehearsal Studio
Comment=Vocal Range Calibration, Real-Time Key Shifting & Post-Take Report Cards
Exec=AppRun %U
Icon=tiki-icon
Type=Application
Categories=AudioVideo;Audio;AudioVideoEditing;
Terminal=false
StartupWMClass=TikiRehearsalLounge
EOF

cp "$APPDIR/tiki-rehearsal-lounge.desktop" "$APPDIR/usr/share/applications/tiki-rehearsal-lounge.desktop"

cat << EOF > "$APPDIR/AppRun"
#!/bin/bash
SELF=\$(readlink -f "\$0")
HERE=\${SELF%/*}
export APPDIR="\${HERE}"
export STUDIO_VERSION="\${STUDIO_VERSION:-${VERSION}}"

# Preserve host LD_LIBRARY_PATH for spawned external tools (mpv, ffmpeg, pw-record, parecord)
export LD_LIBRARY_PATH_ORIG="\${LD_LIBRARY_PATH}"
unset LD_LIBRARY_PATH

export PATH="\${HERE}/usr/bin:\${HERE}/usr/bin/bin:\${PATH}"

cd "\${HERE}/usr/bin"
exec "\${HERE}/usr/bin/TikiRehearsalLounge" "\$@"
EOF

chmod +x "$APPDIR/AppRun"
chmod +x "$APPDIR/usr/bin/TikiRehearsalLounge"
ln -sf AppRun "$APPDIR/TikiRehearsalLounge"

echo "=== [4/5] Packaging AppImage ==="
if [ ! -f "/tmp/appimagetool" ]; then
    curl -sL https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage -o /tmp/appimagetool
    chmod +x /tmp/appimagetool
fi

export ARCH=x86_64
/tmp/appimagetool --appimage-extract-and-run "$APPDIR" "$OUT_APPIMAGE"
chmod +x "$OUT_APPIMAGE"

echo "=== [5/5] Cleaning up temporary build files ==="
docker run --rm -v "${BUILD_TMP}:/b" debian:bookworm-slim rm -rf /b 2>/dev/null || true
rm -rf "$BUILD_TMP" "$APPDIR" 2>/dev/null || true

echo "================================================================="
echo "✅ AppImage build complete: ${OUT_APPIMAGE}"
ls -lh "$OUT_APPIMAGE"
echo "================================================================="
