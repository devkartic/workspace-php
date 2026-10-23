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
release, locked production dependencies, and frontend assets in CI or a project
build stage. The workspace does not install Laravel or run Composer, migrations,
seeding, or asset builds automatically. The sole included project, `itechorbit`,
is a PHP starter rather than an installed Laravel application. Create or register
additional projects with `make project-add` when needed.
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
`openssl rand -hex 32`. Production passwords must have at least 32 characters from
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
chmod 600 docker/.env.production projects/main/.docker/infra.production.env
sudo chown "$(id -u):33" projects/main/.env
chmod 640 projects/main/.env
```

Repeat permissions/scaffolding for api. PHP-FPM uses UID/GID 33 in the default Debian
PHP image; adjust permissions if your custom image uses another user. Private
infrastructure files contain independently generated root passwords and project
resource limits. Existing files are never overwritten.

Production source is baked into the PHP image and its filesystem is read-only.
Application `.env` is mounted read-only; named runtime volumes are initialized from
the image with `www-data` ownership. Existing volume contents are not replaced by
new image seed files. Nginx reads public uploads through a read-only storage mount.
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
rule/session before enabling or changing firewall rules. No project service publishes
production database/cache ports, so do not open 3306/6379 publicly. Docker-published
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

Startup builds only the selected project's images, validates Nginx, waits for its
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

## Add a service or deploy a new release

Project-owned `.docker/Dockerfile`, `.docker/nginx.Dockerfile`, and Compose overrides
are independent. See [the project guide](../PROJECT_GUIDE.md) for worker/extra-service
examples and override conventions. Project settings do not rebuild other projects.
Changing shared templates only affects future scaffolding; existing projects must
apply relevant security/template updates explicitly.

```sh
make backup ENV=production NAME=api
# Install the reviewed release and update WORKSPACE_IMAGE_TAG in api's .env.
make project-up ENV=production NAME=api
```

Only that project's images and containers are reconciled, including removed custom
services. The gateway is not touched. Add/remove domains explicitly with
`make gateway-sync ENV=production`; the gateway attaches to per-project edge networks
and gracefully reloads routes without recreation. `gateway-up` may recreate Caddy
when its image/ports/resource configuration changes. `gateway-down` stops all public
routes; `project-down NAME=api` stops only api and retains all data volumes.

Deployment is not zero downtime. Keep reviewed source revisions and image tags for
rollback. Restore the previous release/tag and rerun project-up to roll back code;
schema changes require an application-specific rollback plan. No migrations, seeding,
workers, or schedulers are installed automatically. Restart long-running workers
as part of each release.

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

Tune limits in each project's `.docker/infra.production.env`, then apply them with
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
`python3 tests/production_smoke.py` runs an opt-in two-project Docker test with unique
stack identities, temporary source/data, and no published host ports. It verifies
separate images/volumes/networks, cross-backend connection blocking, gateway hot
attachment/reload, alias redirects and collision rollback, image secret exclusion,
uploads, Redis authentication, backup/restore, and an independent worker rebuild/shutdown. It cleans up only its own
resources and does not contact public certificate authorities.
