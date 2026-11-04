FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852 AS trust

# Bootstrap HTTPS trust from Ubuntu's signed package repository, before switching
# to the snapshot service (which redirects HTTP requests to HTTPS).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates=20240203

FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
COPY --from=trust /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# Freeze the package repository as well as the base image and signing tools.
RUN sed -i -E 's@http://(archive.ubuntu.com/ubuntu|security.ubuntu.com/ubuntu|ports.ubuntu.com/ubuntu-ports)/?@https://snapshot.ubuntu.com/ubuntu/20261029T000000Z/@g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates python3 cmake ninja-build git openssl openjdk-21-jre-headless \
       gcc libc6-dev libssl-dev libcurl4-openssl-dev zlib1g-dev pkg-config \
    && apt-get clean

COPY scripts/release/install-tools.py scripts/release/tools.lock.json /opt/release-tools/
RUN python3 /opt/release-tools/install-tools.py

RUN useradd --create-home --uid 10001 release
USER release
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/tmp
ENTRYPOINT ["cmake"]
CMD ["--workflow", "--preset", "release_plan_container"]
