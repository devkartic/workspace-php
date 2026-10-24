# Independent PHP Docker projects

Run PHP or Laravel applications on a workstation or VPS. Each project owns its PHP
and Nginx images, MariaDB, Redis, backend network, and data volumes. Only the Caddy
HTTP/HTTPS gateway is shared. Rebuilding or stopping one project leaves others alone.

`itechorbit` is the included fresh Laravel 13 application. Its source and standard
dependencies live in `projects/itechorbit/src/`, independently of workspace and
Docker configuration. Register additional applications with `make project-add`;
existing code and Docker customizations are preserved.

## Requirements

- Docker Engine/Desktop with Compose v2.23.1+ (database access uses inline configs)
- Python 3.9+, Make, and a POSIX shell
- Internet access for initial image builds

Production instructions target Ubuntu 24.04 on Contabo. Projects share the host
kernel, Docker daemon, CPU, RAM, and disk; use separate VMs for host-level isolation.

## Local development

From the repository root:

```sh
make setup
make project-add NAME=api DOCUMENT_ROOT=public
make project-up NAME=api
```

Omitting `PHP` selects the tested stable default, **PHP 8.5**. Use `PHP=8.4`
when an application needs an older supported branch. The selected version is
saved in its settings; existing projects never upgrade implicitly. The default is
maintained with the workspace rather than discovered online during creation.

Open the localhost URL printed by registration. `make setup` prepares the included
`itechorbit` workspace and Laravel environment files but starts no containers.
Install its locked dependencies and generate a local application key before starting:

```sh
cd projects/itechorbit/src
composer install
php artisan key:generate
npm ci
npm run build
cd ../../..
make project-up NAME=itechorbit
```

The `project-add` command above creates a separate new project; use it whenever
you are ready to add another application.

For hostname routing, add `127.0.0.1 api.local` to your local `/etc/hosts`, then run
`make gateway-up`. Open `http://api.local`. Add later routes with `make gateway-sync`.

Register an existing application without replacing its code or environment values:

```sh
make project-add NAME=shop PROJECT_PATH=/srv/apps/shop DOCUMENT_ROOT=public
```

Existing applications must configure their project-local database and Redis
credentials before startup. See [configuration and customization](PROJECT_GUIDE.md).

## Deploy with a real domain

Follow the [Ubuntu/Contabo production guide](docs/PRODUCTION.md) for prerequisites,
credentials, permissions, and first deployment. The essential domain mapping is:

| Layer | Example for project `main` |
| --- | --- |
| DNS at your authoritative DNS provider | `@ A YOUR_VPS_IPV4` and `www CNAME example.com` |
| Application `.env` | `WORKSPACE_HOSTNAME=example.com` |
| Optional aliases in the same `.env` | `WORKSPACE_DOMAIN_ALIASES=www.example.com` |
| Laravel canonical URL | `APP_URL=https://example.com` |
| Workspace `docker/.env.production` | `PRODUCTION_PROJECTS="main api"` and your `ACME_EMAIL` |

DNS points traffic to the VPS; the gateway maps each hostname to its project.
Aliases redirect to the canonical HTTPS domain with HTTP 308 and preserve the URI.
Another project can use `api.example.com` with an A record pointing to the same VPS.
Every canonical name and alias must be unique across selected projects.

After completing the production guide's configuration:

```sh
make validate ENV=production NAME=main
make project-up ENV=production NAME=main
make gateway-up ENV=production
```

Caddy obtains and renews certificates automatically when DNS and ports 80/443 are
reachable. Use `make gateway-sync ENV=production` after hostname/alias changes;
ordinary project builds never restart the gateway. Public DNS and certificate
issuance must be verified on your VPS.

## Nested application source and separate environments

The included project's structure is:

```text
projects/itechorbit/
├── .docker/                    Docker configuration and private infrastructure
├── .env                        Workspace settings only
├── logs/                       Host PHP error logs
└── src/                        Fresh Laravel application
    ├── .env                    Laravel settings only
    ├── composer.json
    ├── artisan
    ├── app/
    └── public/
```

The outer environment sets `WORKSPACE_SOURCE_MODE=mounted` to select project-local
Docker configuration with mounted source, plus `WORKSPACE_SOURCE_ROOT=src`,
`WORKSPACE_DOCUMENT_ROOT=public`, and runtime paths `storage bootstrap/cache`.
The inner environment contains only Laravel application settings. Actual environment
files are private; each directory has a separate committed `.env.example`.
The included project keeps its mounted-runtime Docker configuration in
`projects/itechorbit/.docker/`, outside `src/`; private migration
backups are kept separately under `docker/backups/config/itechorbit/`.

To register another application using the same layout:

```sh
make project-add NAME=shop PROJECT_PATH=/srv/apps/shop SOURCE_ROOT=src DOCUMENT_ROOT=public
```

Here `/srv/apps/shop` is the project wrapper and `/srv/apps/shop/src` is the Laravel
application. Existing root-based registrations remain supported; omitted
`SOURCE_ROOT` defaults to `.`. Run Composer and npm inside the application source
directory; run workspace Make commands from this repository's root.

## Source updates without image rebuilds

New projects mount application source into PHP and Nginx. Production source is
read-only inside containers, with persistent volumes for Laravel runtime data.
Update source and compiled frontend assets from your pipeline, then refresh
Laravel caches and restart workers. Docker rebuilds are needed only for runtime
changes such as PHP versions, extensions, or copied server configuration:

```sh
make project-build ENV=production NAME=api
make project-up ENV=production NAME=api
```

`project-up` builds missing runtime images on initial startup and otherwise reuses
them. Existing projects with application-local `.docker/` and no
`WORKSPACE_SOURCE_MODE=mounted` keep the old behavior
until [explicit migration](docs/PRODUCTION.md#move-existing-docker-configuration-outside-the-application).
See the [pipeline workflow](docs/PRODUCTION.md#deploy-source-and-design-updates-from-a-pipeline)
for source updates and Laravel cache handling.

## Everyday commands

Append `ENV=production` for production. Run commands from the repository root.

| Command | Purpose |
| --- | --- |
| `make project-add NAME=api DOCUMENT_ROOT=public` | Create/register an application |
| `make project-config NAME=api` | Scaffold missing project-owned Docker files |
| `make project-up NAME=api` | Build/start this project's services |
| `make project-down NAME=api` | Stop this project; keep data |
| `make project-build NAME=api` | Explicitly rebuild this project's runtime images |
| `make project-migrate-config NAME=api` | Move legacy Docker config outside source and enable source mounts |
| `make project-restart NAME=api` | Restart existing containers; use project-up to apply config changes |
| `make project-logs NAME=api` / `make project-ps NAME=api` | Logs/status |
| `make shell NAME=api` | Open the PHP container shell |
| `make mariadb NAME=api` / `make redis-cli NAME=api` | Project-specific database/cache clients |
| `make db-access-enable NAME=api` | Temporarily enable private Sequel Pro and Adminer endpoints |
| `make db-access-status NAME=api` / `make db-access-disable NAME=api` | Inspect / close database tool access |
| `make validate NAME=api` | Generate and validate one stack and its overrides |
| `make backup NAME=api` | Create a private logical SQL backup |
| `make restore NAME=api FILE=/path/backup.sql` | Restore a trusted dump into this project's database |
| `make project-list` / `make sync` | List projects / validate all selected projects without starting them |
| `make gateway-up` / `make gateway-sync` | Start shared gateway / gracefully reload routes |
| `make gateway-logs` / `make gateway-down` | Gateway logs / explicitly stop all public routing |
| `make up-all` / `make down-all` | Explicit bulk startup/shutdown; gateway remains separate |
| `make doctor` / `make test` | Check prerequisites / run isolated configuration regressions |

Lifecycle aliases `up`, `down`, `build`, `restart`, `logs`, and `ps` also require
`NAME`. No unqualified lifecycle command operates on every project.

## Temporary database access and imports

Database tool access is disabled by default. On your VPS, from the workspace root:

```sh
make db-access-enable ENV=production NAME=main
make db-access-status ENV=production NAME=main
```

Replace `main` with your running project. This starts a separate, project-owned
database proxy on `127.0.0.1:13306` and Adminer on `127.0.0.1:18080`. The application
and MariaDB are not restarted. Keep ports 3306, 13306, and 18080 closed publicly;
SSH is the entry point. The first enable downloads the maintenance images.

In **Sequel Pro**, select its **SSH** tab:

| Field | Value |
| --- | --- |
| MySQL Host | `127.0.0.1` |
| Port | `13306` |
| Username / Password / Database | Project `.env`: `DB_USERNAME` / `DB_PASSWORD` / `DB_DATABASE` |
| SSH Host | Your VPS IP or hostname |
| SSH User / Key / Port | Your normal server login details |

For **Adminer**, run this on your own computer and leave it running:

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18080:127.0.0.1:18080 YOUR_SSH_USER@YOUR_VPS_IP
```

Open **http://127.0.0.1:18080/** in your browser. Select MySQL, server `mariadb`,
and the same application database credentials. Adminer is provided by its own
container; no `adminer.php` upload or application image rebuild is needed.
SSH must allow TCP forwarding. For a custom SSH port, add `-p PORT` to the command.

Use Sequel Pro's import feature or Adminer's **Import** page for trusted SQL dumps
targeting this project's database. Adminer allows uploads up to **128 MB**, with
a **300-second** PHP execution limit; larger or slower imports should use the
existing streaming restore command. Copy your SQL file to the VPS, then run:

```sh
make backup ENV=production NAME=main
make restore ENV=production NAME=main FILE=/absolute/path/database.sql
```

Restore can replace data and may partially apply on failure. Stop application writes
for a replacement import and keep a verified backup. GUI tools use the application's
database grants; the SSH restore command uses the database administrator account.

Close tool access when finished:

```sh
make db-access-disable ENV=production NAME=main
```

Disable removes both maintenance containers and disconnects their existing sessions.
It preserves application access, database data, and SSH backup/restore commands.
Project shutdown also disables tool access. Access does not automatically resume
after a host restart; use enable again. These commands manage these endpoints,
not the privileges of someone who already has SSH/Docker administration rights.

For concurrent projects or occupied ports, choose different ports when enabling:

```sh
make db-access-enable ENV=production NAME=api DB_ACCESS_PORT=13307 ADMINER_PORT=18081
```

Use the chosen database port in Sequel Pro and the chosen Adminer port in both
sides of the SSH forwarding rule and browser URL. Status reports the actual ports;
disable needs no port arguments. Disable before changing an enabled project's ports.
On a local workstation, omit `ENV=production` and connect to the localhost endpoints
directly (Sequel Pro's Standard tab); no SSH tunnel is needed.

## Files and customization

```text
Makefile                         Supported command interface
scripts/stacks.py                Single workspace CLI
PROJECT_GUIDE.md                 Settings, images, overrides, and added services
docs/PRODUCTION.md               VPS, real domains, email, backups, and migration
docker/docker-compose.yml        Base Compose scaffolding template
docker/templates/                Other scaffolding templates
docker/project.env.example      Safe defaults for new development registrations
projects/itechorbit/.env         Workspace metadata only (WORKSPACE_* settings)
projects/itechorbit/src/         Laravel source, Laravel .env, dependencies, built assets
projects/itechorbit/logs/        Host PHP error log
projects/itechorbit/.docker/     Project-owned Dockerfiles, Compose, and PHP/Nginx settings
docker/projects/<name>/          Automatic registration fallback for other projects
docker/stacks/                   Private generated runtime and volume ownership state
```

Edit a project's `.docker/Dockerfile` (itechorbit) for its PHP extensions and
its `.docker/compose.override.yml` for additional services. Project-owned files are
copied once and never overwritten automatically. Their duplication is intentional:
changing one project must not change another. Template fixes must be reviewed and
applied to existing projects individually.

Keep `.env`, `.docker/infra.*.env`, `docker/projects/*/infra.*.env`, runtime files, and backups private. Production
images exclude dotenv credentials and logs. New production limits are sized as a
starting point for two light Laravel projects on 4 vCPU/8 GB RAM; see the production
guide for the budget and optional worker example.

Former shared-stack users must follow the [data migration procedure](docs/PRODUCTION.md#migrate-an-existing-shared-stack)
before switching traffic. No existing data is moved or deleted automatically.

PHP errors are visible in each project's `logs/php-error.log` in both environments.
The legacy `dev/php.ini` override has been removed; edit the active project-owned `.docker/`
configuration instead. See [PHP logging](PROJECT_GUIDE.md#php-error-logs) for details.
