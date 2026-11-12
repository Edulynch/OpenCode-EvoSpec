FROM ubuntu:22.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git libicu70 libssl3 libkrb5-3 zlib1g \
        liblttng-ust1 libunwind8 build-essential python3 python3-venv unzip \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /bin/bash runner

USER runner
WORKDIR /home/runner
RUN curl --fail --show-error --location --proto '=https' --tlsv1.2 \
        --output runner.tar.gz \
        https://github.com/actions/runner/releases/download/v2.337.0/actions-runner-linux-x64-2.337.0.tar.gz \
    && echo '70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613  runner.tar.gz' | sha256sum -c - \
    && tar -xzf runner.tar.gz \
    && rm runner.tar.gz \
    && mkdir -p _diag _work

COPY --chmod=0555 entrypoint.sh /usr/local/bin/runner-entrypoint
ENV HOME=/home/runner
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/runner-entrypoint"]
