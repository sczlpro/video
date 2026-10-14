FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量，确保 Python 输出无缓冲与中文 UTF-8 支持
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PORT=8000 \
    HOST=0.0.0.0

# 安装系统级依赖：ffmpeg (用于0帧硬切合成母带) 与基础运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖定义并安装
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 复制整个项目源码
COPY . /app/

# 预先确保静态资源与视频输出目录存在
RUN mkdir -p /app/static/assets /app/static/videos /app/static/videos/temp

# 暴露服务端口
EXPOSE 8000

# 启动命令
CMD ["python", "main.py"]
