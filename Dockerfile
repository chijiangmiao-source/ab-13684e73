FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUDIT_PORT=8080

WORKDIR /srv

COPY app ./app
COPY tests ./tests
COPY scripts ./scripts

RUN python -m compileall -q app tests scripts

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python -c "import json,os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('AUDIT_PORT','8080')+'/health',timeout=3).read()" || exit 1

CMD ["python", "-m", "app.server"]
