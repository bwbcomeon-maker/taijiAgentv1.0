FROM ubuntu:20.04

LABEL io.taiji.release-evidence.role="offline-rehearsal-v1" \
      io.taiji.release-evidence.baseline="ubuntu-20.04" \
      io.taiji.release-evidence.network="runtime-none-required"

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive

# This apt transaction is image-build time only; the runner never has a package
# repository or network and uses only local dpkg bytes.
RUN test "$TARGETARCH" = "amd64" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dpkg \
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
