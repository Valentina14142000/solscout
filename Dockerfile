FROM python:3.12-slim
WORKDIR /app
COPY solscout ./solscout
ENV SOLSCOUT_HOST=0.0.0.0 \
    SOLSCOUT_DB_PATH=/data/solscout.db \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "solscout", "serve"]
