# Baked relay image — the reproducible alternative to installing adb at pod start.
# Build and push to the in-cluster registry, then point the Deployment at it and
# drop the apk-install entrypoint:
#
#   docker build -t <registry>/photos-relay:0.1.0 relay/
#   docker push  <registry>/photos-relay:0.1.0
#
# Pinned base + pinned packages keep it reproducible (the repo's versioning rule).
FROM alpine:3.20.3

RUN echo "https://dl-cdn.alpinelinux.org/alpine/v3.20/community" >> /etc/apk/repositories \
    && apk add --no-cache android-tools python3

COPY relay.py /code/relay.py

ENV STATE_PATH=/state/relay-state.json
ENTRYPOINT ["/bin/sh", "-c", "mkdir -p /root/.android \
  && cp /adb-key/adbkey /root/.android/adbkey \
  && cp /adb-key/adbkey.pub /root/.android/adbkey.pub \
  && chmod 600 /root/.android/adbkey \
  && adb start-server \
  && exec python3 /code/relay.py"]
