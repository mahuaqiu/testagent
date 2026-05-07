#!/bin/bash
# Mac 打包脚本 (Nuitka)

set -e

VERSION=${1:-"2.0.0"}
OUTPUT_DIR="dist/macos"

echo "=========================================="
echo "Building Test Worker for macOS (Nuitka)"
echo "Version: $VERSION"
echo "Output: $OUTPUT_DIR"
echo "=========================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python3 not found!"
    exit 1
fi

# 创建虚拟环境
VENV_PATH="build_env_nuitka"
echo "[1/6] Creating virtual environment..."
python3 -m venv $VENV_PATH
source $VENV_PATH/bin/activate

# 安装依赖
echo "[2/6] Installing dependencies..."
pip install --upgrade pip
pip install nuitka ordered-set zstandard
pip install -e ".[all]"

# 生成版本文件
echo "[3/6] Generating version file..."
BUILD_VERSION=$(date +"%Y%m%d%H%M")
echo "VERSION = \"$BUILD_VERSION\"" > worker/_version.py

# 安装 Playwright 浏览器
echo "[4/6] Installing Playwright browsers..."
playwright install chromium || echo "Warning: Playwright browser installation failed"

# 打包 (Nuitka)
echo "[5/6] Building executable with Nuitka..."

python -m nuitka \
    --mode=standalone \
    worker/gui_main.py \
    --output-filename=test-worker \
    --macos-create-app-bundle \
    --macos-app-icon=assets/icon.icns \
    --include-data-dir=config=config \
    --include-data-dir=assets=assets \
    --include-data-dir=tools=tools \
    --enable-plugin=pyqt5 \
    --include-package=uvicorn \
    --include-package=fastapi \
    --include-package=starlette \
    --include-package=httpx \
    --include-package=playwright \
    --include-package=pyautogui \
    --include-package=mss \
    --include-package=cv2 \
    --include-package=PIL \
    --include-package=numpy \
    --include-package=pydantic \
    --include-package=pystray \
    --include-module=pystray._darwin \
    --include-package=six \
    --include-package=uiautomator2 \
    --include-package-data=uiautomator2 \
    --nofollow-import-to=pytest \
    --nofollow-import-to=allure \
    --nofollow-import-to=faker \
    --output-dir=dist/nuitka_build \
    --show-progress

# 清理版本文件
rm -f worker/_version.py

# 创建发布包
echo "[6/6] Creating release package..."
PACKAGE_DIR="$OUTPUT_DIR/test-worker"
mkdir -p "$PACKAGE_DIR"

BUILD_DIR="dist/nuitka_build/gui_main.dist"
if [ -d "$BUILD_DIR" ]; then
    cp -r "$BUILD_DIR/"* "$PACKAGE_DIR/"
else
    echo "Error: Build directory not found: $BUILD_DIR"
    exit 1
fi

# 复制 Playwright chromium
PLAYWRIGHT_CHROMIUM=$(find ~/Library/Caches/ms-playwright -name "chromium-*" -type d | head -1)
if [ -n "$PLAYWRIGHT_CHROMIUM" ]; then
    mkdir -p "$PACKAGE_DIR/playwright"
    cp -r "$PLAYWRIGHT_CHROMIUM" "$PACKAGE_DIR/playwright/"
fi

# 复制 minicap static 文件
MINICAP_SRC="worker/platforms/minicap/static"
MINICAP_TARGET="$PACKAGE_DIR/worker/platforms/minicap/static"
mkdir -p "$MINICAP_TARGET"
cp -r "$MINICAP_SRC/"* "$MINICAP_TARGET/"

# 创建版本文件
echo "$BUILD_VERSION" > "$PACKAGE_DIR/VERSION"

# 创建启动脚本
cat > "$PACKAGE_DIR/start.sh" << 'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
./test-worker.app/Contents/MacOS/test-worker --port 8080
EOF
chmod +x "$PACKAGE_DIR/start.sh"

# 创建 README
cat > "$PACKAGE_DIR/README.txt" << EOF
Test Worker v$BUILD_VERSION - macOS (Nuitka Build)

Usage:
  1. Open Terminal
  2. cd to this directory
  3. Run: ./start.sh

Or run directly:
  ./test-worker.app/Contents/MacOS/test-worker --port 8080

For help:
  ./test-worker.app/Contents/MacOS/test-worker --help

Configuration:
  Edit config/worker.yaml to customize settings
EOF

# 清理
echo "Cleaning up..."
deactivate
rm -rf $VENV_PATH dist/nuitka_build

echo "=========================================="
echo "Build complete!"
echo "Package: $PACKAGE_DIR"
echo "=========================================="