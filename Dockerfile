FROM --platform=linux/amd64 ubuntu@sha256:281c5745f657873d78e5531fc5ba8575f46ab7769b94550ac99543f122679986

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates xvfb x11vnc novnc websockify fluxbox fontconfig python3 x11-utils \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
    libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 blackcoin \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash blackcoin \
    && install -d -m 0755 /usr/local/lib/blackcoin-gui /usr/share/doc/blackcoin-gui \
    && install -d -o 1000 -g 1000 -m 0700 /home/blackcoin/.blackcoin \
    && dpkg-query -W > /usr/share/doc/blackcoin-gui/runtime-packages.txt
COPY --chmod=0755 bin/ /usr/local/bin/
COPY --chmod=0644 runtime/supervise.py /usr/local/lib/blackcoin-gui/supervise.py
COPY --chmod=0755 runtime/start-gui.sh /home/blackcoin/start-gui.sh
COPY --chmod=0644 LICENSE COPYING.core /usr/share/doc/blackcoin-gui/
RUN chmod 0755 /usr/local/bin /usr/local/lib/blackcoin-gui /usr/share/doc/blackcoin-gui
ENV HOME=/home/blackcoin DISPLAY=:0 PYTHONUNBUFFERED=1
USER 1000:1000
WORKDIR /home/blackcoin
RUN QT_QPA_PLATFORM=offscreen blackcoin-qt -version
ARG CORE_VERSION
ARG CORE_SOURCE
ARG CORE_ARCHIVE_SHA256
ARG WRAPPER_REVISION
LABEL org.opencontainers.image.title="Blackcoin Unraid GUI" \
      org.opencontainers.image.source="https://github.com/Blackcoin-Dev/unraid-templates" \
      org.opencontainers.image.version="${CORE_VERSION}" \
      org.opencontainers.image.revision="${WRAPPER_REVISION}" \
      org.blackcoin.core.source="${CORE_SOURCE}" \
      org.blackcoin.core.archive.sha256="${CORE_ARCHIVE_SHA256}"
EXPOSE 8080/tcp 15714/tcp
VOLUME ["/home/blackcoin/.blackcoin"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["/home/blackcoin/start-gui.sh"]
