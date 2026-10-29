FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OFFICE_AGENT_DATA_DIR=/data
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 office \
    && useradd --uid 10001 --gid office --no-create-home office \
    && mkdir /data && chown office:office /data

COPY --chown=office:office app/ ./app/
USER office
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
