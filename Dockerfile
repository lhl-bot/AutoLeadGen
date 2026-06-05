FROM python:3.9-slim

WORKDIR /app

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 使用阿里云镜像源加速 apt 和 pip（国内服务器必须）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    true

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（用阿里云 PyPI 镜像）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 复制源代码
COPY . .

# 暴露端口
EXPOSE 8001

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8001/health', timeout=5).raise_for_status()" || exit 1

# 使用 gunicorn + uvicorn worker 生产级启动
CMD ["gunicorn", "main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "1", \
     "--bind", "0.0.0.0:8001", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
