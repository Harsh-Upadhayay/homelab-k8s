# Baked relay image — the reproducible alternative to fetching adb at pod start.
# Build and push to the in-cluster registry, then point the Deployment at it and
# drop the download entrypoint:
#
#   docker build -t <registry>/photos-relay:0.1.0 relay/
#   docker push  <registry>/photos-relay:0.1.0
#
# glibc base + Google's static platform-tools adb — load-bearing, not incidental:
# Alpine's musl android-tools adb (libusb 1.0.27) assert-crashes opening this
# handset over QEMU USB passthrough ("failed to open device: Input/Output Error"),
# while Google's static adb opens the identical device cleanly. Do not "simplify"
# this to an Alpine base.
FROM python:3.12-slim-bookworm

ADD https://dl.google.com/android/repository/platform-tools-latest-linux.zip /tmp/pt.zip
RUN python3 -c "import zipfile; zipfile.ZipFile('/tmp/pt.zip').extractall('/opt')" \
    && chmod 755 /opt/platform-tools/adb \
    && rm /tmp/pt.zip

COPY relay.py /code/relay.py

ENV STATE_PATH=/state/relay-state.json \
    ADB=/opt/platform-tools/adb
ENTRYPOINT ["/bin/sh", "-c", "mkdir -p /root/.android \
  && cp /adb-key/adbkey /root/.android/adbkey \
  && cp /adb-key/adbkey.pub /root/.android/adbkey.pub \
  && chmod 600 /root/.android/adbkey \
  && ($ADB start-server || true) \
  && exec python3 /code/relay.py"]
