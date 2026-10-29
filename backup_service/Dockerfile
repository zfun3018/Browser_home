FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BACKUP_STAGING_DIR=/staging

WORKDIR /srv/backup
RUN apk add --no-cache postgresql16-client \
    && addgroup -S backup && adduser -S -G backup backup \
    && mkdir -p /staging && chown -R backup:backup /staging /srv/backup
COPY backup_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backup_service/ ./backup_service/
USER backup
EXPOSE 8010
CMD ["uvicorn", "backup_service.app:app", "--host", "0.0.0.0", "--port", "8010"]
