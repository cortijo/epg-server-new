FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1 LICENSE_DATA_DIR=/data LICENSE_HTTP_PORT=9200
RUN addgroup -g 10002 license && adduser -D -u 10002 -G license -h /data license
COPY license-server/app.py /app/app.py
RUN chmod 0755 /app/app.py
WORKDIR /data
VOLUME ["/data"]
EXPOSE 9200/tcp
USER 10002:10002
ENTRYPOINT ["python3", "/app/app.py"]
