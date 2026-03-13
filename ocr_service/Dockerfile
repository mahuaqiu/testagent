# OCR Service Dockerfile
# 基于 PaddleOCR 的文字识别和图像匹配服务

FROM python:3.10-slim

LABEL maintainer="OCR Service"
LABEL description="OCR and Image Matching Service"

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCR_HOST=0.0.0.0 \
    OCR_PORT=8081 \
    OCR_LANG=ch

# 安装系统依赖
# libgomp1: OpenMP 支持（OpenCV 需要）
# libgl1-mesa-glx, libglib2.0-0: OpenCV GUI 依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建缓存目录
RUN mkdir -p /app/cache

# 暴露端口
EXPOSE 8081

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

# 启动命令
CMD ["python", "-m", "ocr_service.server"]