FROM ubuntu:20.04

LABEL io.taiji.release-evidence.role="offline-rehearsal-v1" \
      io.taiji.release-evidence.baseline="ubuntu-20.04" \
      io.taiji.release-evidence.fixture="kylin-os-release-v1" \
      io.taiji.release-evidence.network="runtime-none-required"

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive

COPY ubuntu20-required-system-packages.tsv /usr/local/share/taiji-ubuntu20-required-system-packages.tsv

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
    && cut -f2 /usr/local/share/taiji-ubuntu20-required-system-packages.tsv \
        | xargs -r apt-get install -y --no-install-recommends \
    && python3 -c 'import ctypes,pathlib; mapping=pathlib.Path("/usr/local/share/taiji-ubuntu20-required-system-packages.tsv"); [ctypes.CDLL(line.split("\t",1)[0]) for line in mapping.read_text().splitlines() if line]' \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash rehearsal \
    && printf '%s\n' 'rehearsal ALL=(root) NOPASSWD: ALL' > /etc/sudoers.d/taiji-rehearsal \
    && chmod 0440 /etc/sudoers.d/taiji-rehearsal

COPY run-lifecycle.sh /usr/local/bin/run-lifecycle.sh
RUN chmod 0755 /usr/local/bin/run-lifecycle.sh

USER root
ENTRYPOINT ["/usr/local/bin/run-lifecycle.sh"]
