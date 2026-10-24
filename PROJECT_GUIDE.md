# Configuration and customization

The Makefile is the supported interface. Every command delegates to the single
`scripts/stacks.py` CLI. Settings are read literally from dotenv files; shell
expressions and variable interpolation are never executed by the workspace parser.
Laravel still interprets its own application environment normally.

## Ownership and settings

Registration preserves existing application code and environment values. External
sources are linked under `projects/<name>`. The included itechorbit application uses
`projects/itechorbit/.docker/` for Docker configuration and `src/` for Laravel.
Automatic registrations without an explicit source mode use `docker/projects/<name>/`. Existing application-local `.docker/`
registrations without `WORKSPACE_SOURCE_MODE=mounted` keep their legacy build
behavior until explicitly migrated; see
[production migration](docs/PRODUCTION.md#move-existing-docker-configuration-outside-the-application).
Failed registration restores existing `.env` bytes and removes only newly created registration files and links.

For nested projects, the parent `.env` contains only `WORKSPACE_*` metadata and
`src/.env` contains only application settings. The CLI reads both without writing
workspace metadata into Laravel. `WORKSPACE_DOCUMENT_ROOT` and writable paths are
relative to the application source; `logs/php-error.log` stays relative to the
parent project. `${PROJECT_SOURCE}` resolves to the application source,
`${PROJECT_ROOT}` to the parent, and `${PROJECT_CONFIG}` to the active Docker configuration directory.
Compose override relative paths continue resolving from the parent project root.
Use `SOURCE_ROOT=src` with `make project-add` for this layout; existing application
environment values are preserved. Development setup copies each missing environment
file from its own example and does not replace existing values.

| Workspace setting | Meaning |
| --- | --- |
| `WORKSPACE_SOURCE_MODE` | Set `mounted` to use project-local `.docker/` runtime configuration with source mounts; omit for existing automatic location/legacy behavior |
| `WORKSPACE_SOURCE_ROOT` | Application directory relative to the registered project; `src` for itechorbit, `.` when absent for compatibility |
| `WORKSPACE_PROJECT_NAME` | Stable lowercase name matching `projects/<name>`; maximum 32 characters; `gateway` is reserved |
| `WORKSPACE_PHP_VERSION` | Default: 8.5; development: 7.4, 8.1, 8.3, 8.4, 8.5; production: 8.3, 8.4, 8.5 |
| `WORKSPACE_HOSTNAME` | Canonical hostname, without scheme, port, or path |
| `WORKSPACE_DOMAIN_ALIASES` | Optional space-separated hostnames redirecting to the canonical hostname |
| `WORKSPACE_DOCUMENT_ROOT` | Relative web directory; use `public` for Laravel |
| `WORKSPACE_HTTP_PORT` | Optional development-only localhost port; ignored in production |
| `WORKSPACE_IMAGE_TAG` | Runtime image tag, default `current`; source updates need no tag change |
| `WORKSPACE_WRITABLE_PATHS` | Existing relative directories outside the public root; mounted Laravel defaults to storage and bootstrap/cache when absent |
| `WORKSPACE_STORAGE_VOLUME` / `WORKSPACE_CACHE_VOLUME` | Optional existing Laravel storage/cache volumes to adopt explicitly |

Set `APP_URL` to the canonical URL. Canonical names and aliases must be lowercase
literal DNS hostnames; URLs, wildcard names, IP addresses, and duplicate mappings
are rejected. Production requires public hostnames. Locally, add each used name to
`/etc/hosts`. Local aliases redirect over HTTP, including the configured gateway
port; production aliases redirect over HTTPS. Both use HTTP 308, preserving the
request method, path, and query string.

Each stack resolves `mariadb:3306` and `redis:6379` to its own containers. Configure
an explicit non-root `DB_USERNAME`, `DB_PASSWORD`, and `REDIS_PASSWORD`. Generated
new-project defaults are development-only. Existing application values are preserved
and must be updated explicitly when migrating from shared/root access. Database
names/users may repeat between projects because database servers are separate.

The private `projects/<name>/.docker/infra.<environment>.env` contains an independently generated
MariaDB root password and this project's resource limits. Existing private files
are never overwritten. Changing a file's password does not alter initialized
MariaDB accounts; rotate accounts explicitly and update the corresponding file.

## What to edit

| File | Purpose |
| --- | --- |
| `projects/<name>/.docker/Dockerfile` | This project's PHP extensions, packages, and build stages |
| `projects/<name>/.docker/nginx.Dockerfile` | Nginx runtime image; public assets come from the source mount |
| `projects/<name>/.docker/compose.yml` | Project-owned base service configuration |
| `projects/<name>/.docker/compose.override.yml` | Common project-only additions/overrides |
| `projects/<name>/.docker/compose.override.production.yml` | Optional production-specific override |
| `projects/<name>/.docker/compose.override.development.yml` | Optional development-specific override |
| `projects/<name>/.docker/php.ini`, `projects/<name>/.docker/production.ini` | Project-owned PHP settings |
| `projects/<name>/.docker/fpm.conf`, `projects/<name>/.docker/production-fpm.conf` | PHP-FPM settings |
| `projects/<name>/.docker/nginx.conf.template` | Routing rules for this project's Nginx |
| `projects/<name>/.docker/*Dockerfile.dockerignore` | Build exclusions; keep credentials/log exclusions |

Scaffolding copies missing files from `docker/templates/` and the base Compose
template. Existing files remain independent. Keep the development/production build
targets and FPM healthcheck contracts when editing Dockerfiles. Official MariaDB/
Redis image layers may share Docker's cache, while containers and storage remain
separate. Existing projects require explicit review/application of later template
or security updates.

Mounted project-local runtime images use `projects/<name>/.docker/` as their build context and contain
no application source. PHP and Nginx mount the registered source; production code
is read-only, while configured writable runtime paths use persistent volumes.
Prepare Composer dependencies and frontend assets in CI before publishing source.
Application `.dockerignore` rules do not apply to runtime-only builds. Private
`infra.*.env` and override files are excluded from the runtime build context.
`project-up` builds missing runtime images and reuses existing ones; use
`project-build` followed by `project-up` after runtime configuration changes.
Ordinary source updates need only Laravel cache refreshes and worker restarts.

Generated files under `docker/stacks/<environment>/<project>/` are private and
replaced by synchronization. Do not edit them or delete the volume ownership
registry during cleanup. PHP configuration lives in `projects/<name>/.docker/php.ini` and
`projects/<name>/.docker/production.ini`; the legacy `dev/php.ini` override is no longer loaded.

## Add services to one project

Example `projects/<name>/.docker/compose.override.yml`:

```yaml
services:
  rabbitmq:
    image: rabbitmq:4-alpine
    restart: unless-stopped
    mem_limit: 512m
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
volumes:
  rabbitmq_data: {}
```

Configure the service's own credentials using project-private settings. Its default
network is this project's backend, and its volume is namespaced to this stack.
Project overrides cannot use shared/external networks, unapproved external volumes,
host namespaces, privileged containers, added capabilities, devices, host sockets,
or bind mounts outside the registered source, external configuration, or generated
runtime directory. Secret/config files must belong to the project.
Custom runtime builds must use `${PROJECT_CONFIG}` as context and Dockerfile paths
relative to it. Additional build contexts and host/shared build networks are unsupported.
Production host ports and writable host bind mounts are rejected except the PHP
container's dedicated project `logs/` mount; development ports must bind to `127.0.0.1`. These checks guard the supported workflow, not users who
already control the Docker daemon.

All override paths resolve from the parent project root, not `projects/<name>/.docker/`. Merge order:
project base Compose, generated environment overlay, common override, then optional
environment-specific override. Project settings and generated identity/path values
take precedence over exported shell values; application dotenv keys cannot redirect
the Docker transport or override Compose controls.

```sh
make validate ENV=production NAME=api
make project-up ENV=production NAME=api
```

Only this project is reconciled; missing runtime images are built. Removed services are removed within its
Compose identity. Other projects and the gateway are untouched.

## Optional Laravel worker and scheduler

Scaffolding provides `projects/<name>/.docker/compose.override.production.yml.example`. Once the
project is a prepared Laravel application with
`WORKSPACE_WRITABLE_PATHS=storage bootstrap/cache`, copy the example to
`compose.override.production.yml` **only if that file does not already exist**.
Otherwise merge its worker service into the existing override.

The optional worker uses this project's PHP image and runtime volumes, runs as
`www-data`, rotates logs, and is limited to 256 MiB/0.5 CPU. Its PHP memory ceiling
is 192 MB and it recycles between jobs above 128 MB or after one hour. Configure the
application queue driver and credentials; keep Laravel's queue `retry_after` longer
than the worker's 60-second timeout. Tune the budget for heavier jobs. Workers are
not enabled by default and should restart after release/configuration updates.

A host cron example for a prepared Laravel project:

```cron
* * * * * cd /srv/php-workspace && WORKSPACE_ENV=production python3 scripts/stacks.py compose api exec -T -u www-data php php artisan schedule:run >> /var/log/api-scheduler.log 2>&1
```

Set cron's PATH and log permissions/rotation. The CLI supports inspection and
application commands, for example:

```sh
WORKSPACE_ENV=production python3 scripts/stacks.py compose api config --quiet
WORKSPACE_ENV=production python3 scripts/stacks.py compose api exec -T -u www-data php php artisan queue:restart
```

Use lifecycle Make commands for builds/startup/shutdown; direct CLI Compose verbs
are limited to inspection and `exec` so validation is retained.

## Gateway and locks

`gateway-up` starts Caddy, attaches it to each project's separate edge network,
and reloads routes. It mounts no application source/secrets and has no direct
backend connection. `gateway-sync` validates new routes and reloads without
recreating Caddy; gateway resource/port changes need explicit `gateway-up` and can
recreate it. Alias collisions or invalid configuration preserve active gateway files.

`project-down` retains its external edge network and data. A stopped project's
canonical domain returns 502; other domains keep working. `down-all` does not stop
the gateway. `gateway-down` is an explicit action affecting all public routes.

Generation uses a workspace lock; deployments use project-specific locks. Logs and
interactive shells do not hold configuration locks. If a process is forcibly
killed, the error reports its actual lock path. Remove a stale lock only after
confirming its command is no longer running.

See the [production guide](docs/PRODUCTION.md) for real-domain DNS, HTTPS, deployment,
resource budgeting, SMTP, backups, and migration.

## PHP error logs

Both environments write PHP errors to `logs/php-error.log` in the application root,
mounted at `/var/log/project` in the PHP container. `make project-up` creates the
folder/file before FPM starts, preserves existing contents, and sets ownership to
`www-data` with directory mode 755 and file mode 644. The deployment user can read
logs; administrative permissions may be needed to rotate/delete them on Linux.
Symlink directories and log files are rejected. Do not replace this mount in overrides.

```sh
tail -f projects/itechorbit/logs/php-error.log
```

`log_errors=On` enables logging; `error_log` specifies the destination. FPM pins the
same destination using `php_admin_value[error_log]`. Production keeps display of
errors off. Laravel retains `storage/logs`; Nginx/FPM service diagnostics remain in
container output. The optional queue-worker example also keeps CLI PHP errors in
container output (`make project-logs NAME=itechorbit`, with `ENV=production` on VPS).
PHP errors now go to the file rather than Docker output. `/logs/` is blocked by
Nginx, and log contents are excluded from Git and image builds.

For applications in separate Git repositories, also ignore `logs/*` there.

New projects receive these settings automatically. Existing projects keep their
independent `.docker` files: apply the logging settings, `prepare-logs` script and
Dockerfile copy, Nginx deny rules, and build exclusions explicitly when upgrading.
Move any historical `dev/logs/` files into `logs/` before removing the old directory.
For production file rotation, see [the production guide](docs/PRODUCTION.md#php-error-log-rotation).
