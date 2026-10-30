# Backend image. Deploys unchanged to Render (free web service) and to a Hugging Face Docker Space,
# which is why the port is read from the environment rather than fixed: Render injects $PORT, a
# Space expects 7860.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    HF_HOME=/tmp/hf

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# Baked into the image rather than downloaded at boot: the filesystem is not persistent, so a
# cold start would otherwise pull ~90 MB before serving its first request.
RUN python scripts/fetch_embedding_model.py

# Spaces run the container as a non-root user, and the app writes uploads under storage/.
RUN mkdir -p storage && chmod -R 777 storage

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
