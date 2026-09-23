FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY tests/ ./tests/
COPY verify.py ./

EXPOSE 8080

# 默认启动 HTTP 服务；verify 单次服务通过命令覆盖入口
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "2", "--timeout", "30", "app.web:app"]
