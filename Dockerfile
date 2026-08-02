FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY music_video_grabber ./music_video_grabber
RUN pip install --upgrade pip && pip install .

USER 1000:1000

EXPOSE 8080

CMD ["uvicorn", "music_video_grabber.main:app", "--host", "0.0.0.0", "--port", "8080"]
