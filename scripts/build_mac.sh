#!/bin/bash
# Mac 打包脚本

set -e

VERSION=${1:-"2.0.0"}
OUTPUT_DIR="dist/macos"

echo "=========================================="
echo "Building Test Worker for macOS"
echo "Version: $VERSION"
echo "Output: $OUTPUT_DIR"
echo "=========================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python3 not found!"
    exit 1
fi

# 创建虚拟环境
echo "[1/6] Creating virtual environment..."
python3 -m venv build_env
source build_env/bin/activate

# 安装依赖
echo "[2/6] Installing dependencies..."
pip install --upgrade pip
pip install -e ".[all]"
pip install pyinstaller

# 安装 Playwright 浏览器
echo "[3/6] Installing Playwright browsers..."
playwright install chromium || echo "Warning: Playwright browser installation failed"

# 打包
echo "[4/6] Building executable..."
pyinstaller scripts/pyinstaller.spec --clean --noconfirm

# 创建发布包
echo "[5/6] Creating release package..."
PACKAGE_DIR="$OUTPUT_DIR/test-worker-$VERSION"
mkdir -p "$PACKAGE_DIR"

# 复制文件
cp dist/test-worker "$PACKAGE_DIR/"
cp -r config "$PACKAGE_DIR/"

# 创建启动脚本
cat > "$PACKAGE_DIR/start.sh" << 'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
./test-worker --port 8080
EOF
chmod +x "$PACKAGE_DIR/start.sh"

# 创建 README
cat > "$PACKAGE_DIR/README.txt" << EOF
Test Worker v$VERSION - macOS

Usage:
  1. Open Terminal
  2. cd to this directory
  3. Run: ./start.sh

Or run directly:
  ./test-worker --port 8080 --ocr-service http://localhost:8081

For help:
  ./test-worker --help

Configuration:
  Edit config/worker.yaml to customize settings
EOF

# 清理
echo "[6/6] Cleaning up..."
deactivate
rm -rf build_env build dist/*.spec

echo "=========================================="
echo "Build complete!"
echo "Package: $PACKAGE_DIR"
echo "=========================================="