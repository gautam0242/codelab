# Free-tier friendly image with g++ so students' C++ can be compiled on this server (EXECUTOR=local).
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ tini && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV APP_ENV=production EXECUTOR=local PYTHONUNBUFFERED=1
# tini reaps zombie processes left by killed student programs. The app runs as root so it can run student code as 'nobody'.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 180"]
