FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# Root bo'lmagan foydalanuvchi ostida ishlaydi
RUN useradd --create-home --uid 1000 pulbot \
    && mkdir -p /app/data \
    && chown -R pulbot:pulbot /app
USER pulbot

CMD ["python", "-m", "bot"]
