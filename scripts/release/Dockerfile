FROM node:26.13.1-alpine3.24@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5

COPY scripts/release/install-packages.sh scripts/release/packages-*.lock /opt/release-tools/
RUN sh /opt/release-tools/install-packages.sh \
    && rm -rf /usr/local/lib/node_modules /opt/yarn-* \
       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
       /usr/local/bin/yarn /usr/local/bin/yarnpkg

COPY scripts/release/install-tools.py scripts/release/tools.lock.json /opt/release-tools/
RUN python3 /opt/release-tools/install-tools.py

RUN adduser -D -u 10001 release
USER release
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["cmake"]
CMD ["--workflow", "--preset", "release_plan_container"]
