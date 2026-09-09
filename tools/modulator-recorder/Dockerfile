FROM mcr.microsoft.com/playwright:v1.55.0-noble

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    RECORDER_DIR=/recordings \
    RECORDER_PORT=6080

RUN apt-get update && apt-get install -y --no-install-recommends \
    fluxbox novnc websockify x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY tools/modulator-recorder/start-recorder.sh /usr/local/bin/start-recorder
RUN chmod 0755 /usr/local/bin/start-recorder \
    && mkdir -p /recordings \
    && chown -R pwuser:pwuser /recordings

USER pwuser
VOLUME ["/recordings"]
EXPOSE 6080/tcp
ENTRYPOINT ["/usr/local/bin/start-recorder"]
