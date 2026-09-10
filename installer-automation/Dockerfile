FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10003 --home /app --shell /usr/sbin/nologin installer
WORKDIR /app
COPY installer-automation/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY installer-automation/app.py ./app.py
USER 10003:10003
EXPOSE 9300
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9300/health', timeout=3)"
CMD ["python3", "/app/app.py"]
