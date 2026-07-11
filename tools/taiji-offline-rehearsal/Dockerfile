FROM debian:13-slim

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive

RUN test "$TARGETARCH" = "amd64" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        file \
        findutils \
        gzip \
        iproute2 \
        lsof \
        procps \
        python3 \
        sudo \
        systemd \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash rehearsal \
    && printf '%s\n' 'rehearsal ALL=(root) NOPASSWD: ALL' > /etc/sudoers.d/taiji-rehearsal \
    && chmod 0440 /etc/sudoers.d/taiji-rehearsal

COPY run-lifecycle.sh /usr/local/bin/run-lifecycle.sh
RUN chmod 0755 /usr/local/bin/run-lifecycle.sh

USER root
ENTRYPOINT ["/usr/local/bin/run-lifecycle.sh"]
