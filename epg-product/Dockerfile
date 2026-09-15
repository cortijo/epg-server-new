FROM ubuntu:24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ pkg-config libboost-system-dev libboost-thread-dev \
    libcurl4-openssl-dev libjsoncpp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY src/EpgOnlyMain.cpp src/EpgInjector.cpp src/EpgInjector.h \
     src/ConfigManager.cpp src/ConfigManager.h src/utils.cpp src/utils.h ./src/
RUN mkdir -p /out && g++ -std=c++17 -O2 -DNDEBUG -pthread \
    -I/usr/include/jsoncpp -Isrc \
    src/EpgOnlyMain.cpp src/EpgInjector.cpp src/ConfigManager.cpp src/utils.cpp \
    -lboost_system -lboost_thread -lcurl -ljsoncpp \
    -o /out/TVStreamEpgOnly

FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    EPG_DATA_DIR=/data \
    EPG_HTTP_PORT=9100 \
    EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
    EPG_INSTALL_MODE=docker \
    EPG_UPDATE_REPOSITORY=cortijo/epgserver2 \
    EPG_UPDATE_TOKEN_FILE=/update/update.token
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 python3-pil libboost-system1.83.0 libboost-thread1.83.0 \
    libcurl4t64 libjsoncpp25 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /data --shell /usr/sbin/nologin epgstream
COPY --from=build /out/TVStreamEpgOnly /app/TVStreamEpgOnly
COPY epg-product/app.py /app/app.py
COPY epg-product/license_client.py /app/license_client.py
COPY epg-product/assets /app/assets
COPY scripts/verify_isdbtb_ts.py /app/verify_isdbtb_ts.py
RUN chmod 0755 /app/TVStreamEpgOnly /app/app.py /app/license_client.py /app/verify_isdbtb_ts.py
WORKDIR /data
VOLUME ["/data"]
EXPOSE 9100/tcp
STOPSIGNAL SIGTERM
USER 10001:10001
ENTRYPOINT ["python3", "/app/app.py"]
