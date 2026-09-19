FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 py3-aiohttp py3-cryptography py3-pip \
    && python3 -m pip install --break-system-packages --no-cache-dir aiomqtt==2.5.1

WORKDIR /app
COPY imou_bridge /app/imou_bridge
COPY run.sh /app/run.sh
RUN chmod 0755 /app/run.sh

CMD ["/app/run.sh"]
