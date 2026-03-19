#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Marginalia"
BUILD_DIR="/tmp/${APP_NAME}-build"
APP_BUNDLE="${BUILD_DIR}/${APP_NAME}.app"
INSTALL_DIR="/Applications"

echo "Building ${APP_NAME}..."

# Check dependencies
if ! command -v swiftc &> /dev/null; then
    echo "Error: Xcode Command Line Tools required. Install with: xcode-select --install"
    exit 1
fi

if ! command -v uv &> /dev/null; then
    echo "Error: uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if ! python3 -c "from PIL import Image" &> /dev/null; then
    echo "Error: Pillow required for icon generation. Install with: pip3 install Pillow"
    exit 1
fi

# Clean
rm -rf "$BUILD_DIR"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

# Compile Swift binary
echo "Compiling..."
swiftc -o "$APP_BUNDLE/Contents/MacOS/${APP_NAME}" \
    "$PROJECT_DIR/marginalia.swift" \
    -framework Cocoa

# Generate icon
echo "Generating icon..."
python3 - <<'PYEOF'
from PIL import Image, ImageDraw

size = 1024
img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

margin, r = 80, 180
draw.rounded_rectangle([margin, margin, size-margin, size-margin], radius=r, fill=(245, 243, 240, 255))

cx, cy = size // 2, size // 2 + 20
draw.polygon([(cx-20,cy-180),(cx-280,cy-140),(cx-280,cy+200),(cx-20,cy+240)], fill=(60,60,60,255))
draw.polygon([(cx+20,cy-180),(cx+280,cy-140),(cx+280,cy+200),(cx+20,cy+240)], fill=(80,80,80,255))

for y_off in [-100,-50,0,50,100]:
    draw.rounded_rectangle([cx-250,cy+y_off-4,cx-60,cy+y_off+4], radius=4, fill=(200,195,188,255))
    draw.rounded_rectangle([cx+60,cy+y_off-4,cx+250,cy+y_off+4], radius=4, fill=(210,205,198,255))

draw.line([(cx,cy-180),(cx,cy+240)], fill=(245,243,240,255), width=6)
img.save('/tmp/marginalia_icon.png')
PYEOF

# Convert to icns
ICONSET="/tmp/marginalia.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
for s in 16 32 64 128 256 512 1024; do
    sips -z $s $s /tmp/marginalia_icon.png --out "$ICONSET/icon_${s}x${s}.png" > /dev/null 2>&1
done
for s in 16 32 128 256 512; do
    d=$((s*2))
    cp "$ICONSET/icon_${d}x${d}.png" "$ICONSET/icon_${s}x${s}@2x.png" 2>/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns"

# Write Info.plist with embedded project path
cat > "$APP_BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.marginalia.app</string>
    <key>CFBundleVersion</key>
    <string>0.2.0</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <false/>
    <key>ProjectDir</key>
    <string>${PROJECT_DIR}</string>
</dict>
</plist>
EOF

# Install to /Applications
echo "Installing to ${INSTALL_DIR}..."
rm -rf "${INSTALL_DIR}/${APP_NAME}.app"
cp -r "$APP_BUNDLE" "${INSTALL_DIR}/${APP_NAME}.app"

# Clean up temp files
rm -rf "$BUILD_DIR" "$ICONSET" /tmp/marginalia_icon.png

echo "Done! ${APP_NAME} installed to ${INSTALL_DIR}/${APP_NAME}.app"
echo "Project directory: ${PROJECT_DIR}"
