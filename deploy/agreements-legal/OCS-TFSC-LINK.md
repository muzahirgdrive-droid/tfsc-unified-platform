# Separate OCS change: attach the OCS API to `tfsc-link`

**This is a change to the OCS stack, not to TFS Agreements.** `agreements.sh update` never makes it, never restarts OCS
and never edits OCS files. Make it yourself, in the OCS installation, after reading "Impact" below. It can be done before
or after the Legal update.

## Why

OCS reads the Legal feed at `OCS_AGREEMENTS_URL=http://tfsc-agreements-legal:8000/api/integration/v1`. That name exists
only on the Docker network `tfsc-link`, where the Legal web service has the alias `tfsc-agreements-legal`. The OCS API
container is not on `tfsc-link`, so the name does not resolve.

After the Legal update, the Legal side is ready: `tfsc-link` exists (created if it was missing) and `legal-agreements`
is on it with the alias `tfsc-agreements-legal`. The feed is never published to the internet.

## The persistent change (OCS compose file)

1. Find the OCS API service and its current networks (read-only):
   ```
   docker ps --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}' | grep -i ocs
   docker inspect -f '{{json .NetworkSettings.Networks}}' <ocs-api-container>      # every network it is on now
   docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' <ocs-api-container>   # its compose file
   ```
2. In that compose file, under the OCS API service, list **every network it is on now** plus `tfsc-link`. If the service
   has no `networks:` key today, it is on the project's `default` network only, and `default` **must** be written out,
   or the container would lose it:
   ```yaml
   services:
     <ocs-api-service>:          # the OCS API service, exactly as named in that file
       networks:
         default: {}             # keep: the network(s) it is on now (one line each, with their aliases if any)
         tfsc-link: {}           # new: reach Legal as tfsc-agreements-legal

   networks:
     tfsc-link:
       external: true
       name: tfsc-link
   ```
   If the file already has a top-level `networks:` section, add the `tfsc-link` entry to it, and do not repeat the key.
3. Check the file, then recreate **only** the OCS API container, from the OCS folder:
   ```
   docker compose config -q                              # must print nothing
   docker network inspect tfsc-link >/dev/null || docker network create tfsc-link   # only if the Legal update has not run yet
   docker compose up -d --no-deps <ocs-api-service>
   ```
   No `down`, no `--remove-orphans`, no other service.
4. Verify (no token needed; 401 means "reachable, token required"):
   ```
   docker exec <ocs-api-container> python3 -c "import urllib.request as u,urllib.error as e
   try: u.urlopen('http://tfsc-agreements-legal:8000/api/integration/v1/meta',timeout=8)
   except e.HTTPError as x: print(x.code)"
   ```
   It must print `401` (if the container has no python3: `docker exec <ocs-api-container> getent hosts tfsc-agreements-legal`
   must print an address). `sudo ./agreements.sh preflight` in the Legal folder then shows the OCS container "on tfsc-link".

## Impact

- `docker compose up -d --no-deps <ocs-api-service>` **recreates the OCS API container** (a restart of a few seconds;
  requests to the OCS API during those seconds fail). Its volumes, its database and the other OCS services are not
  touched, and Legal is not affected.
- The OCS API gains one network. Nothing else is published, no port opens, and the feed stays private.
- To undo: remove the `tfsc-link` lines and run the same `up -d --no-deps <ocs-api-service>`.

### Optional: connect now without a restart (does not survive a recreation)

`docker network connect tfsc-link <ocs-api-container>` attaches the running container immediately, without a restart.
It is lost the next time that container is recreated, so make the persistent change above as well.
