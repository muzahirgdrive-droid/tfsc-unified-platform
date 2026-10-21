# PostgreSQL 16 (the same Alpine image as before, so existing data directories keep their collation) plus pgBackRest
# for encrypted base backups, continuous WAL archiving and point-in-time recovery (docs/runbooks/backups-and-restore.md).
# Alpine's pgbackrest package depends on a whole PostgreSQL 18 server, so only its binary is copied over.
# Behind a TLS-inspecting proxy: docker build --secret id=build_ca,src=/path/to/ca.crt ...
FROM postgres:16-alpine AS pgbackrest
RUN --mount=type=secret,id=build_ca,required=false \
    cp /etc/ssl/certs/ca-certificates.crt /tmp/ca.orig; \
    if [ -s /run/secrets/build_ca ]; then cat /run/secrets/build_ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    apk add --no-cache pgbackrest; status=$?; cp /tmp/ca.orig /etc/ssl/certs/ca-certificates.crt; exit $status

FROM postgres:16-alpine
RUN --mount=type=secret,id=build_ca,required=false \
    cp /etc/ssl/certs/ca-certificates.crt /tmp/ca.orig; \
    if [ -s /run/secrets/build_ca ]; then cat /run/secrets/build_ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    apk add --no-cache libssh2 libxml2 lz4-libs zstd-libs libbz2 jq; status=$?; \
    cp /tmp/ca.orig /etc/ssl/certs/ca-certificates.crt; rm /tmp/ca.orig; exit $status
COPY --from=pgbackrest /usr/bin/pgbackrest /usr/bin/pgbackrest
COPY pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
COPY tfsc-backup /usr/local/bin/tfsc-backup
# /backups holds the repository, locks, logs and restore-test reports; /restore is the restore test's scratch space.
RUN mkdir -p /backups /restore && chown postgres:postgres /backups /restore && chmod 700 /backups /restore \
    && chmod 755 /usr/local/bin/tfsc-backup && pgbackrest version
