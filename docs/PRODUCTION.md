# Production on Contabo Ubuntu 24.04

Each application has its own PHP/Nginx images, MariaDB, authenticated Redis,
networks, and volumes. Only Caddy's HTTPS gateway is shared. Backend traffic is
isolated; the VPS kernel, Docker daemon, CPU, RAM, and disk remain shared.

All examples run from the repository root. Replace `example.com`, `main`, `api`,
paths, and `203.0.113.10` with your actual domains, project names, and VPS IPv4.
The sample IP is a documentation address, not a deployable server address.

## 1. Prepare the VPS and applications

Use Ubuntu 24.04 LTS with Python 3.9+, Make, and Docker Compose v2. Install Docker
Engine and the Compose plugin using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
Install the other tools and place your repository at `/srv/php-workspace`:

```sh
sudo apt-get update
sudo apt-get install -y make python3 git openssl dnsutils curl
cd /srv/php-workspace
make doctor
make setup ENV=production
```

Use a production checkout separate from local development. Prepare each Laravel
release, locked production dependencies, and frontend assets in CI before publishing
the prepared source directory. The workspace does not install Laravel or run
Composer, migrations,
seeding, or asset builds automatically. The included project, `itechorbit`, is a
fresh Laravel 13 application under `projects/itechorbit/src/`. Install its locked
Composer dependencies and build frontend assets before production deployment.
Create or register additional projects with `make project-add` when needed.
Existing shared-stack users should read [migration](#migrate-an-existing-shared-stack)
before starting new database containers or switching traffic.

Set these workspace values in `docker/.env.production`:

```dotenv
COMPOSE_PROJECT_NAME=php-workspace
PRODUCTION_PROJECTS="main api"
ACME_EMAIL=admin@example.com
NGINX_HTTP_PORT=80
EDGE_CPUS=0.5
EDGE_MEMORY=256m
```

The namespace is part of every image, network, and volume identity. Preserve it once
data exists. Legacy global MariaDB/Redis credentials are no longer used.

## Included itechorbit layout

`projects/itechorbit/.env` contains workspace settings only. In production set its
`WORKSPACE_SOURCE_MODE=mounted`, `WORKSPACE_SOURCE_ROOT=src`,
`WORKSPACE_DOCUMENT_ROOT=public`,
`WORKSPACE_WRITABLE_PATHS="storage bootstrap/cache"`, PHP version, canonical host,
and aliases. Set `APP_ENV`, `APP_DEBUG`, `APP_URL`, `APP_KEY`, database, mail, and
Redis settings exclusively in `projects/itechorbit/src/.env`. Neither actual
`.env` is committed; copy each directory's own `.env.example` when preparing a new
checkout. Never put `WORKSPACE_*` assignments in Laravel's environment file.

Docker settings live at `projects/itechorbit/.docker/`, outside the Laravel `src/` checkout. The application root mounted
at `/var/www/html` is **src**, so Laravel serves `/var/www/html/public/index.php`.
PHP's host error log remains `projects/itechorbit/logs/php-error.log`; Laravel's own
logs live in the storage runtime volume. Preserve existing infrastructure secrets
and runtime volumes when deploying this layout to an existing server. For itechorbit, `WORKSPACE_SOURCE_MODE=mounted` selects the project-local
`.docker/` runtime configuration without changing source-mount behavior. Projects
without this setting retain their existing configuration selection. Preserve or
explicitly migrate old private infrastructure before switching layouts.

For an existing VPS where Laravel currently lives directly in `projects/itechorbit`,
first back up its source, environment, infrastructure configuration, and runtime
data. Publish the reviewed application into `src/`, preserving its Laravel-only
`.env`; move only `WORKSPACE_*` settings into the parent `.env`, set
`WORKSPACE_SOURCE_ROOT=src`, and migrate legacy Docker configuration before
`project-up`. Existing volume names and credentials stay unchanged. Do not replace
your existing application with the fresh example or regenerate its `APP_KEY`.

Run Composer/npm inside `projects/itechorbit/src`. Laravel's local example uses file
sessions/cache and synchronous queues so the welcome page needs no database
migration. Generate a new `APP_KEY` only for a genuinely new application. Production
Laravel `.env` permissions apply to `projects/itechorbit/src/.env`:

```sh
sudo chown "$(id -u):33" projects/itechorbit/src/.env
chmod 640 projects/itechorbit/src/.env
chmod 600 projects/itechorbit/.env
```

The `main`/`api` examples below retain the supported root-based registration layout.
To use a nested layout for them, add `SOURCE_ROOT=src` to registration, keep the
parent environment workspace-only, and put application settings under `src/.env`.

## 2. Configure and register Laravel projects

For a new registration, configure the application's existing `.env` connection
settings first. The default Laravel root database user must be replaced with a
project-specific non-root user. Example `/srv/apps/main/.env` values:

```dotenv
APP_ENV=production
APP_DEBUG=false
APP_URL=https://example.com
DB_HOST=mariadb
DB_PORT=3306
DB_DATABASE=main
DB_USERNAME=main_user
DB_PASSWORD=REPLACE_WITH_A_UNIQUE_RANDOM_SECRET
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=REPLACE_WITH_ANOTHER_UNIQUE_RANDOM_SECRET
REDIS_PREFIX=main:
```

Generate a separate database and Redis password for every project with
`openssl rand -hex 8` (16 hexadecimal characters). Production passwords must have
at least 16 characters from
`A-Z`, `a-z`, `0-9`, `_`, and `-`; placeholders are rejected. Set the application's
own `APP_KEY`, cache/session drivers, mail settings, and proxy trust as appropriate.
The image has no PHP Redis extension; install `predis/predis` or customize its build.

Register the applications without starting development containers. Omitting `PHP`
uses the tested stable default, PHP 8.5; pass `PHP=8.4` explicitly when required:

```sh
make project-add NAME=main PROJECT_PATH=/srv/apps/main DOCUMENT_ROOT=public HOST=example.com
make project-add NAME=api PROJECT_PATH=/srv/apps/api DOCUMENT_ROOT=public HOST=api.example.com
```

Configure api's application credentials separately before its registration. Existing
registrations skip these commands. Registration preserves application values and
appends workspace metadata. Edit existing assignments rather than appending duplicate
keys. For main, the resulting workspace settings should include:

```dotenv
WORKSPACE_PROJECT_NAME=main
WORKSPACE_PHP_VERSION=8.5
WORKSPACE_HOSTNAME=example.com
WORKSPACE_DOMAIN_ALIASES=www.example.com
WORKSPACE_DOCUMENT_ROOT=public
WORKSPACE_WRITABLE_PATHS=storage bootstrap/cache
WORKSPACE_IMAGE_TAG=release_20261024
```

For api, use `WORKSPACE_PROJECT_NAME=api`, `WORKSPACE_HOSTNAME=api.example.com`,
`APP_URL=https://api.example.com`, and leave aliases empty unless needed. Each name
must belong to only one selected project. Use one canonical hostname in `APP_URL`;
aliases redirect to it with HTTP 308, preserving the request method and full URI.

Require a dedicated public directory and existing real runtime directories outside
it. Laravel must serve `public/index.php`, with writable `storage` and
`bootstrap/cache`; see [Laravel deployment](https://laravel.com/docs/13.x/deployment).
Complete production settings before scaffolding:

```sh
make project-config ENV=production NAME=main
chmod 600 docker/.env.production docker/projects/main/infra.production.env
sudo chown "$(id -u):33" projects/main/.env
chmod 640 projects/main/.env
```

Repeat permissions/scaffolding for api. PHP-FPM uses UID/GID 33 in the default Debian
PHP image; adjust permissions if your custom image uses another user. Private
infrastructure files contain independently generated root passwords and project
resource limits. Existing files are never overwritten.

New projects keep Docker configuration in `docker/projects/<name>/`, outside the
application checkout. Production PHP and Nginx mount the source directory read-only
at `/var/www/html`; application code and public assets are never copied into their
runtime images. The application `.env` is included in that read-only mount.
Named runtime volumes overlay `storage` and `bootstrap/cache`; empty volumes are
seeded from host source at startup and initialized with `www-data` ownership.
Existing volume contents are preserved. Mounted Laravel projects default to these
two writable directories when `WORKSPACE_WRITABLE_PATHS` is absent; an explicit
setting must include both, unless existing runtime volumes are adopted.
Legacy projects with an application-local `.docker/` and no explicit mounted mode
retain image-baked source until
explicitly migrated using the procedure below. Nginx reads public uploads through
a read-only storage mount.
Laravel's `public/storage -> ../storage/app/public` is supported; PHP execution
under `/storage/` is denied. Custom upload locations need equivalent routing rules.

## 3. Map real domains in DNS

Edit records at your domain's **authoritative DNS provider**, which may be Contabo,
your registrar, or another DNS service. Hosting the VPS at Contabo does not require
moving the domain or its nameservers there.

| Type | Name | Value | Result |
| --- | --- | --- | --- |
| A | `@` | `203.0.113.10` | `example.com` reaches the VPS |
| CNAME | `www` | `example.com.` | `www.example.com` reaches the same VPS |
| A | `api` | `203.0.113.10` | `api.example.com` reaches the VPS for another project |

Some DNS interfaces require full names rather than `@`/`www`/`api`, and some omit
the final dot automatically. Replace conflicting A/CNAME records for the same name.
Only add AAAA records after verifying public IPv6 reaches this VPS and the gateway;
a stale AAAA record can break requests or certificate validation.

**DNS and application configuration are separate:** a CNAME does not redirect the
browser. `WORKSPACE_HOSTNAME` and `WORKSPACE_DOMAIN_ALIASES` tell Caddy which project
owns each incoming hostname and which names redirect. No VPS `/etc/hosts` change is
needed for real public domains.

Check resolution before requesting certificates:

```sh
dig +short A example.com
dig +short CNAME www.example.com
dig +short A www.example.com
dig +short A api.example.com
dig +short AAAA example.com
```

The A answers must resolve to your real VPS address. Wait for changed records to
propagate according to their TTL. If using a DNS proxy/CDN, verify direct DNS-only
routing first, then configure the proxy's origin TLS settings separately.

## 4. Allow HTTP/HTTPS and start the stacks

In the Contabo firewall, allow incoming TCP 80/443 and your SSH port from appropriate
sources. If Ubuntu UFW is active, allow HTTP/HTTPS there too. Preserve a working SSH
rule/session before enabling or changing firewall rules. No application project service publishes
production database/cache ports. Optional database administration uses a separate
stack bound only to localhost; do not open 3306/6379 or its maintenance ports publicly. Docker-published
ports can bypass UFW; retain the restricted Compose exposure and provider firewall.
See [Docker's firewall notes](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).

Make sure no existing web server occupies ports 80/443. Start each independent
project, then the gateway:

```sh
make validate ENV=production NAME=main
make project-up ENV=production NAME=main
make validate ENV=production NAME=api
make project-up ENV=production NAME=api
make gateway-up ENV=production
```

Startup builds missing runtime images for only the selected project, reuses existing
runtime images, validates Nginx, waits for its
MariaDB/Redis, provisions and verifies database credentials, then starts its services.
The gateway attaches to separate edge networks without accessing project backends.

Caddy manages certificates and HTTP-to-HTTPS redirects. Its certificate/config
volumes persist across shutdowns. Real DNS, reachable 80/443, outbound CA access,
and persistent writable certificate storage are required; see
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).
Do not add a separate Certbot service or manually edit generated Caddy files.

After changing a canonical hostname or aliases, keep `APP_URL` synchronized, apply
the application configuration, and explicitly reload gateway routes:

```sh
make project-up ENV=production NAME=main
make gateway-sync ENV=production
```

Gateway sync validates the candidate and gracefully reloads routes without recreating
Caddy. Domain collisions or invalid candidate files preserve active configuration.
Changes to the gateway image, ports, or resource limits require `gateway-up` and may
recreate it. Ordinary project builds never operate on the gateway.

## 5. Verify mapping and Laravel behavior

```sh
curl -I http://example.com
curl -I https://example.com
curl -I 'https://www.example.com/check?source=www'
curl -I https://api.example.com
make project-ps ENV=production NAME=main
make gateway-logs ENV=production
```

HTTP should redirect to HTTPS. The alias should return **308** with
`Location: https://example.com/check?source=www`. The canonical project's application
controls its own response status. Certificate validation must pass without `-k`.
For diagnosis after certificate issuance, bypass DNS using
`curl --resolve example.com:443:YOUR_VPS_IPV4 -I https://example.com`.

Refresh Laravel's runtime caches after a release or `.env` change, using the
application user; restart configured queue workers afterward:

```sh
WORKSPACE_ENV=production python3 scripts/stacks.py compose main exec -T -u www-data php php artisan optimize
WORKSPACE_ENV=production python3 scripts/stacks.py compose main exec -T -u www-data php php artisan queue:restart
```

Run migrations explicitly through your application's deployment procedure after
backups/review. Configure Laravel's proxy trust for the actual Caddy/Nginx path,
secure sessions, and application-level health checks. Service health is not an
application integration test.

| Symptom | Check |
| --- | --- |
| Domain reaches another server | Authoritative A/CNAME/AAAA records, DNS caches, and any proxy/CDN |
| Alias serves nothing | Alias DNS plus `WORKSPACE_DOMAIN_ALIASES`, then gateway-sync |
| Certificate issuance fails | Public DNS/IPv6, ports 80/443, conflicting web servers, restrictive CAA records, gateway logs |
| Canonical domain returns 502 | Project service health, logs, and gateway edge-network attachment |
| Laravel returns 500 | Application key/config, dependencies, database credentials, and runtime permissions |
| Settings appear unchanged | Laravel configuration cache and long-running workers |
| Gateway sync reports a collision | Remove the duplicate canonical/alias assignment from selected projects |

## Move existing Docker configuration outside the application

Existing registrations with `projects/<name>/.docker/` and no
`WORKSPACE_SOURCE_MODE=mounted` continue using their original build behavior.
The included itechorbit project already uses mounted runtime configuration and
does not need this legacy migration command. Migrate each project explicitly, from the workspace root:

```sh
make backup ENV=production NAME=api
make project-migrate-config ENV=production NAME=api
make project-up ENV=production NAME=api
```

The legacy migration command (for projects without `WORKSPACE_SOURCE_MODE`) copies
configuration and private infrastructure credentials into `docker/projects/api/`, validates the candidate, and preserves stack names,
networks, database/cache credentials, and runtime volume identities. It stores a
private configuration backup under `docker/backups/config/api/` and retains
the original application `.docker/` directory. It does not stop running containers.
The following `project-up` builds runtime images once and recreates application
containers with source mounts; this may briefly interrupt requests.

Custom or outdated Dockerfiles/base Compose definitions cause migration to stop
before switching configuration. Compare those files against `docker/templates/`
and `docker/docker-compose.yml`, save custom runtime instructions, and merge the
legacy build contracts before retrying. Reapply custom runtime instructions to the
external Dockerfiles afterward and run `project-build`, then `project-up`. Do not
reintroduce application `COPY` instructions. Overrides remain relative to the
application root. Custom build contexts must use `${PROJECT_CONFIG}`, and custom
Dockerfile paths must be relative to that context. Worker services using the PHP
image must mount `${PROJECT_SOURCE}:/var/www/html:ro` and the same runtime volumes.
Merge the updated worker example when needed. Invalid candidate overrides leave
active generated configuration unchanged.

To revert configuration before further deployments, move the external project
configuration directory aside, restore the saved legacy configuration if needed,
and run `project-up`. The retained legacy setup rebuilds source into images. Keep
source revisions and runtime-volume backups separately; configuration backups do
not include application data. Do not delete volumes during migration or rollback.

## Deploy source and design updates from a pipeline

For new or migrated projects, GitHub Actions and Bitbucket Pipelines can use the
same deployment procedure. Keep the application at a stable host directory such as
`/srv/workspace-php/projects/itechorbit/src`, with Docker configuration independently at
`/srv/workspace-php/projects/itechorbit/.docker/`.

1. Test a reviewed application revision and prepare locked dependencies with
   `composer install --no-dev --prefer-dist --optimize-autoloader`, plus frontend
   assets with `npm ci` and `npm run build`, using a compatible build environment.
2. Publish the prepared source, `vendor/`, and compiled `public/build/` assets into
   the existing application directory. Preserve the server `.env`, workspace
   metadata in the parent directory, parent `logs/`, and host runtime seed directories. Keep the existing storage
   and cache volumes. Do not use a destructive checkout cleanup or publish stale
   environment-specific Laravel cache files from CI.
3. After publishing completes, refresh Laravel's caches and workers through SSH:

   ```sh
   cd /srv/workspace-php
   WORKSPACE_ENV=production python3 scripts/stacks.py compose itechorbit exec -T -u www-data php php artisan optimize
   WORKSPACE_ENV=production python3 scripts/stacks.py compose itechorbit exec -T -u www-data php php artisan queue:restart
   ```

PHP, Blade, CSS/JavaScript builds, and public assets now update through the mounts.
No Docker build, `project-up`, or container recreation is required for ordinary
source updates. PHP's supplied OPcache settings recheck changed files within about
two seconds; Laravel caches still need the refresh above. Restart any additional
long-running application servers through their own deployment procedure. Restart
PHP if changing preload configuration or when custom OPcache settings disable
file timestamp checks.

Publish assets before refreshing caches. Updating files in place can expose a
partially published release, so use application maintenance mode when needed;
this workflow does not promise zero downtime. Restore the previous source and
assets and refresh caches/workers to roll back code. Schema changes need their
own backup and migration/rollback procedure and are never run automatically.

Keep the mounted directory itself stable. Switching a registration symlink to a
different release directory requires `make project-up ENV=production NAME=itechorbit`
to reconcile the changed source path. Replacing a directory at the same pathname
requires stopping and starting that project with `project-down` then `project-up`,
which retains volumes but interrupts service. Existing mounts do not follow
replacement directory inodes automatically.

### Change Docker or PHP runtime configuration

Each project's Dockerfiles, Compose overrides, and PHP/Nginx settings are
independent. See [the project guide](../PROJECT_GUIDE.md) for custom services.
Changing shared templates affects future scaffolding only.

After changing a PHP version, extension, package, Dockerfile, or copied PHP/Nginx
configuration, rebuild and apply only the affected project's runtime images:

```sh
make project-build ENV=production NAME=itechorbit
make project-up ENV=production NAME=itechorbit
```

For Compose/resource settings only, use `project-up`. `WORKSPACE_IMAGE_TAG` now tags
the runtime image, not each source revision; keep it unchanged for ordinary code
updates. Legacy projects still rebuild application images on `project-up`.

The gateway is not touched by ordinary project operations. Add/remove domains with
`make gateway-sync ENV=production`; gateway image/port/resource changes need
`gateway-up`. `project-down NAME=api` retains that project's data volumes.

Database provisioning is idempotent and verifies application logins before exposing
the stack. It never silently rotates initialized account passwords. To change a root
or application password, update the MariaDB account explicitly through
`make mariadb ENV=production NAME=api`, then update the corresponding private file.
Changing a file alone is insufficient. Use new, dedicated application users; reused
users may retain historical grants. Redis password changes apply when its own
container is recreated; update only that project's consumers before restarting it.

## Migrate an existing shared stack

The old shared database/Redis volumes are never reused or deleted automatically.
The isolated database/cache servers start empty. Do not switch routing until each
project's data and credentials have been migrated and verified.

1. Record the old Compose namespace, volume names, database names, credentials, and
   application release. Save the old Compose files and a version-control revision
   from before this migration. Keep an independently verified backup off the VPS.
2. Put the migrating application into maintenance mode and stop its queue workers
   and scheduler, so writes do not continue while its final dump is imported.
3. Export each application database from the still-running old MariaDB container.
   Identify it by its saved namespace/labels, not a guessed container name:

   ```sh
   umask 077
   docker exec OLD_MARIADB_CONTAINER sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb-dump -uroot --single-transaction --quick --hex-blob --routines --events --triggers "$1"' sh api > /secure/path/api.sql
   ```

   Check the command's exit status and verify the dump. Single-transaction dumps
   require transactional tables and no concurrent schema changes. Export one database
   without `--databases` to avoid database-selection statements during restore.
4. Configure the isolated application with a non-root user and unique database/Redis
   passwords, generate its Docker files, and start its stack while the old gateway
   still serves traffic. Production project services have no host ports, so the old
   public gateway can remain running during preparation.
5. Restore the trusted dump with
   `make restore ENV=production NAME=api FILE=/secure/path/api.sql`. Check critical
   row counts and application behavior through its Nginx container or a controlled
   test route. Do not enable public writes before verification.
6. Migrate uploads and runtime data. You may explicitly adopt project-specific old
   storage/cache volumes with `WORKSPACE_STORAGE_VOLUME` and
   `WORKSPACE_CACHE_VOLUME`. A volume can belong to only one project/environment;
   cross-stack adoption is rejected using the private volume ownership registry.
   For separate environments, copy into separate volumes rather than sharing them.
   Do not copy one shared Redis dataset into every project: discard expendable caches
   after review; drain queues or migrate durable project keys with an application-aware
   process. Sessions may require a planned logout window.
7. Repeat for all projects. Once ready, stop the old gateway so ports 80/443 become
   free and start the new gateway. Verify HTTPS, routes, application writes, queues,
   uploads, and backups before leaving maintenance mode.
8. Retain the old database and volumes for the rollback window. To roll back, stop
   the new gateway and restore the saved old stack/routing with its original namespace
   and credentials. Reconcile any writes made after cutover before reverting data.
   Do not run `down -v`, prune volumes, or remove the old data during migration.

Separate development/production Compose identities do not migrate application code,
credentials, or data. Prefer separate checkouts. The runtime ownership registry is
`docker/stacks/volume-owners.json`; change an adopted volume's reservation manually
only after its previous stack has been retired and its data backed up.

## Laravel email

Contabo hosts the applications; it does not supply application SMTP credentials.
Each project can connect outbound to an external SMTP provider with independent
credentials and a verified sender/domain. No incoming SMTP port or shared mail
container is required for this setup. Example private application settings:

```dotenv
MAIL_MAILER=smtp
MAIL_HOST=smtp.your-provider.com
MAIL_PORT=587
MAIL_USERNAME=your-project-smtp-user
MAIL_PASSWORD="your-project-smtp-password"
MAIL_FROM_ADDRESS="notifications@example.com"
MAIL_FROM_NAME="${APP_NAME}"
```

Match encryption settings to your actual `config/mail.php` and provider. Recent
Laravel configurations use `MAIL_SCHEME=smtp` for SMTP/STARTTLS (commonly port 587)
or `smtps` for implicit TLS (commonly port 465); older configurations may read
`MAIL_ENCRYPTION=tls`. See [Laravel mail](https://laravel.com/docs/13.x/mail).
Add the provider's domain-verification/DKIM/SPF records, allow required outbound
traffic, refresh Laravel's configuration cache, and restart queued-mail workers.
Send a test through the application's own mail feature and inspect delivery logs.
`MAIL_MAILER=log` intentionally writes messages to logs rather than delivering them.
Never put real SMTP credentials in examples, images, or version control.

## VPS resource budget

New production infrastructure files target **two light Laravel projects** on
4 vCPU/8 GB RAM. Existing private resource limits remain unchanged.

| Service | Memory ceiling per project |
| --- | --- |
| PHP-FPM | 1 GiB |
| MariaDB | 1 GiB |
| Nginx | 128 MiB |
| Redis | 256 MiB; data limit 128 MB |
| Optional queue worker | 256 MiB |

Two projects with one optional worker each total approximately **5.25 GiB**, plus
256 MiB for Caddy. Leave the remaining memory for Ubuntu, Docker, filesystem cache,
and bursts. These are ceilings, not reservations or measured capacity guarantees.
CPU ceilings stay PHP 1, MariaDB 1, Nginx 0.5, and Redis 0.5 per project; they can
contend on the four-core host when projects peak together. Optional workers have a
0.5 CPU ceiling. Measure realistic workloads before adding projects or concurrency.
PHP retains a 256 MB limit and three FPM workers. Workers are opt-in using the
[project guide's example](../PROJECT_GUIDE.md#optional-laravel-worker-and-scheduler).

## Backups and operations

### Temporary Sequel Pro / Adminer access

Use the [database access and import instructions](../README.md#temporary-database-access-and-imports)
to enable private endpoints on the VPS:

```sh
make db-access-enable ENV=production NAME=api
make db-access-status ENV=production NAME=api
# After finishing your work:
make db-access-disable ENV=production NAME=api
```

Sequel Pro connects through its SSH tab to `127.0.0.1:13306` on the VPS.
Adminer opens at `http://127.0.0.1:18080/` through an SSH tunnel; it is not served
at the public website's `/adminer.php`. Both use the application's database credentials.
Use `DB_ACCESS_PORT` and `ADMINER_PORT` on enable to select different ports.
Default access is disabled. Enabling requires a healthy database and Compose
v2.23.1+; no application image rebuild or database recreation occurs.

Disable terminates both maintenance containers and their active connections.
The application and existing SSH-based backup/restore remain available.
Project-down/down-all also clean up maintenance containers. Maintenance services
do not automatically restart after a host reboot. Normal project startup and
validation never enable them.

| Symptom | Check |
| --- | --- |
| Local port unavailable | Run status; disable before changing an enabled project's ports, or select unused ports |
| SSH forwarding fails | SSH user/key/port and server TCP-forwarding policy; local computer port conflicts |
| Sequel Pro cannot connect | Enable status, SSH tab, MySQL host `127.0.0.1`, configured proxy port, application credentials |
| Adminer cannot connect to database | Server `mariadb`, application database/user/password, and project MariaDB health |
| Adminer import exceeds limits | Use Sequel Pro or the streaming SSH restore command below for larger/longer imports |

### SQL backup and restore

```sh
make backup ENV=production NAME=api
make restore ENV=production NAME=api FILE=/absolute/path/to/backup.sql
```

Backups are private, uncompressed SQL dumps under
`docker/backups/production/api/`. They are streamed to a temporary file and published
atomically only after success. Restore requires an explicit project/file and executes
trusted SQL as database administrator; it can replace tables and may partially apply
on failure. Stop writes and take a fresh backup first. Verify restores against a
separate disposable project/database before relying on them. Back up runtime volumes,
secrets, and gateway certificate data separately; SQL backups do not include them.

Schedule backups under the deployment account, for example:

```cron
0 2 * * * cd /srv/php-workspace && /usr/bin/make backup ENV=production NAME=api >> /var/log/api-backup.log 2>&1
```

Set cron's PATH and log permissions/rotation. Copy successful backups to encrypted
off-server storage with a retention policy. Backups are not deleted automatically.
Configure external HTTPS uptime monitoring, backup-age alerts, disk/memory alerts,
and database/cache monitoring. Healthchecks are available via project-ps; Docker
restart policies do not restart a service solely because it becomes unhealthy.

Tune limits in itechorbit's `projects/itechorbit/.docker/infra.production.env`
(or the other project's active configuration directory), then apply them with
`make project-up ENV=production NAME=api`. Redis uses `noeviction`; monitor memory
errors, especially for queues. Logs rotate at 10 MB with three files per service;
added services need their own limits/logging. Your 100 GB SSD must accommodate
images, build cache, databases, uploads, and retained backups. Monitor disk space
and keep backups off-server; a VPS snapshot alone is not the backup procedure.

For Laravel scheduling, host cron may execute the project wrapper with `-T`:

```cron
* * * * * cd /srv/php-workspace && WORKSPACE_ENV=production python3 scripts/stacks.py compose api exec -T -u www-data php php artisan schedule:run >> /var/log/api-scheduler.log 2>&1
```

Use the project override for persistent workers. Configure framework proxy trust for
the actual Caddy/Nginx path, secure sessions, retry behavior, and application-level
health endpoints. Service healthchecks do not verify business functionality.

## Verification

`make test` runs configuration/registration regressions without starting containers.
`python3 tests/laravel_smoke.py` additionally serves the included Laravel application
inside a disposable nested-source stack (run Composer install first). It checks
the welcome page, live route/assets, split environments, parent PHP logs, and runtime
permissions without changing a live application.
`python3 tests/mounted_smoke.py` runs disposable two-project mounted-source checks
for live PHP/assets, read-only source, writable runtime initialization, persistent
volumes, image reuse, and project isolation. It publishes no host ports and removes
only its own temporary stacks, volumes, networks, and images.
`python3 tests/db_access_smoke.py` additionally checks temporary database access using
a disposable MariaDB, unique backend network, and ephemeral localhost ports. It
verifies SQL import through the proxy, Adminer availability/upload settings, repeated
enable/disable, active connection termination, and continued direct database access.
`python3 tests/production_smoke.py` runs an opt-in two-project Docker test with unique
stack identities, temporary source/data, and no published host ports. It verifies
separate images/volumes/networks, cross-backend connection blocking, gateway hot
attachment/reload, alias redirects and collision rollback, image secret exclusion,
uploads, Redis authentication, backup/restore, and an independent worker rebuild/shutdown. It cleans up only its own
resources and does not contact public certificate authorities.

## PHP error log rotation

Each project exposes PHP errors at its own `logs/php-error.log` on the host, including
production. For itechorbit in the standard workspace location:

```sh
tail -f /srv/php-workspace/projects/itechorbit/logs/php-error.log
```

`make project-up ENV=production NAME=itechorbit` initializes the directory/file with
PHP worker ownership before startup, without truncating existing logs. PHP errors
are logged when `log_errors=On`, while production error display stays off. This is
the sole supported writable production host bind mount; application code remains
read-only. Laravel's `storage/logs` and Nginx container logging are unchanged.

Install logrotate if needed (`sudo apt-get install -y logrotate`). Create
`/etc/logrotate.d/php-workspace-itechorbit` as root, with:

```text
/srv/php-workspace/projects/itechorbit/logs/php-error.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su www-data www-data
}
```

Use a separate entry with the correct absolute path for each project. Check the
configuration without rotating files:

```sh
sudo logrotate --debug /etc/logrotate.d/php-workspace-itechorbit
```

Ubuntu's logrotate scheduler performs rotation; confirm its timer or cron is active.
`copytruncate` preserves the file that PHP has open, with a small possibility of
losing entries written during the copy/truncate window. Include these host log
folders separately in backups when needed; they are not Docker volume data.
