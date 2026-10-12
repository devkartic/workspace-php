# PHP Development Workspace

A Docker workspace for running multiple PHP applications side by side. Each application gets its own PHP-FPM container, PHP version, hostname, and database. Nginx, MariaDB, and Redis are shared.

Use it to start a small PHP project, register an existing application, or work on projects that require different PHP versions.

## Requirements

- Docker Engine with Docker Compose v2, or Docker Desktop, running locally.
- Make and a POSIX shell. Commands below assume macOS, Linux, or a Linux shell in WSL.
- Available host ports: HTTP `80`, MariaDB `3306`, Redis `6380`, and optional phpMyAdmin `8081`. Configure alternatives in `docker/.env`.
- For regression tests only: Python 3 and a local PHP CLI.

PHP and Composer run inside containers; you do not need to install them locally for development. Initial image builds require internet access.

## Quick start

Run from the repository root:

```sh
make setup
make project-add NAME=exampleapp PHP=8.4
make project-up NAME=exampleapp
```

`make setup` checks tools and Docker access, copies `docker/.env.example` only when `docker/.env` is missing, and generates validated configuration. It does not start containers.

Add this entry to your hosts file (`/etc/hosts` on macOS/Linux):

```text
127.0.0.1 exampleapp.local
```

Open the `http://localhost:port` URL shown by registration immediately, or **http://exampleapp.local** after adding the hosts entry. Both addresses serve the same application. Use `make project-list` to see assigned ports. To check routing without editing your hosts file:

```sh
curl -H 'Host: exampleapp.local' http://127.0.0.1/
```

Use the configured HTTP port in the URL when it differs from `80`. To start the included examples instead, run `make setup` and `make up`: `dashboard.local` uses PHP 7.4 and `php84.local` uses PHP 8.4.

## Register an existing application

For an application whose entry point is `public/index.php`:

```sh
make project-add NAME=api PHP=8.4 \
  PROJECT_PATH=/absolute/path/to/api DOCUMENT_ROOT=public
make project-up NAME=api
make shell PROJECT=api
# Inside the container:
composer install
```

Source files and existing application environment values are preserved. Registration appends workspace settings to the application’s `.env` and links external source directories under `projects/<name>`; configure your application's credentials and run its own setup commands as described in [the project guide](PROJECT_GUIDE.md).

## Everyday commands

### itechorbit

The application source is in `projects/itechorbit` inside this workspace.
Its application settings and documented Docker settings share
`projects/itechorbit/.env`. It uses the same shared Compose and Nginx setup as
the other projects. The current PHP starter serves `index.php` from the
project root, and PHP logs are in `dev/logs/`.

Run `make project-up NAME=itechorbit`, then open **http://itechorbit.local** or
**http://localhost:8082**. It uses the existing MariaDB database and Redis service;
no migrations or seeding run. The `./scripts/itechorbit` shortcut remains available
and now controls the shared service. Its `down` command stops only that service.

| Command | Action |
| --- | --- |
| `make help` | Show available commands |
| `make setup` / `make doctor` | Initialize configuration / check prerequisites |
| `make project-add NAME=api PHP=8.4` | Create or register a project |
| `make project-up NAME=api` | Build/start one project and shared services; create missing databases |
| `make project-restart NAME=api` | Recreate one PHP container to apply ini settings |
| `make project-list` | List registered projects |
| `make shell PROJECT=api` | Open a shell for Composer, PHP, and application commands |
| `make up` / `make down` | Start all projects / stop the stack while retaining data |
| `make ps` / `make logs` | Show service status / follow logs |
| `make tools` | Start phpMyAdmin at http://localhost:8081 |
| `make mariadb` / `make redis-cli` | Open database/cache clients |
| `make sync` / `make validate` | Generate configuration / validate Compose |
| `make test` | Run isolated regression tests without starting containers |

The included projects support both URL styles:

| Application | Hostname URL | Port URL |
| --- | --- | --- |
| itechorbit | http://itechorbit.local | http://localhost:8082 |
| dashboard | http://dashboard.local | http://localhost:8083 |
| php84 | http://php84.local | http://localhost:8084 |

New projects automatically receive an unused workspace port starting at `8083`.
Override it with `make project-add NAME=api PHP=8.4 HTTP_PORT=8090` or edit
`WORKSPACE_HTTP_PORT` in `projects/<name>/.env`, then run
`make project-up NAME=api`. Port URLs require no hosts-file entries.

## How the workspace fits together

```text
Browser → Nginx → project PHP-FPM container
                       ├── MariaDB: project database
                       └── Redis: shared cache/queues
```

```text
.
├── Makefile                      Developer commands
├── scripts/
│   ├── workspace                 Setup and prerequisite checks
│   └── projects                  Registration and configuration generation
├── docker/
│   ├── .env.example              Local infrastructure settings template
│   ├── docker-compose.yml        Shared services and optional tools
│   ├── php/                      PHP image recipe and shared ini defaults
│   └── nginx/base.conf           Shared routing and Docker DNS configuration
├── projects/<name>/
│   ├── .env                      Application and Docker workspace settings (ignored)
│   ├── .env.example              Safe defaults for fresh checkouts
│   ├── dev/php.ini               Project PHP settings
│   ├── dev/logs/                 Project debug logs (ignored by Git)
│   └── …                         Application source
└── tests/test_workspace.py       Configuration and preservation checks
```

Generated Compose, Nginx, and SQL files are ignored by Git. MariaDB persists in a Docker volume; Redis persists in `docker/data/redis`. Local `.env` files, backups, and IDE settings are also ignored.

## Further reading

- [Project guide](PROJECT_GUIDE.md): configuration, framework setup, daily work, troubleshooting, and data recovery.
- [Code review](docs/CODE_REVIEW.md): reviewed files, resolved issues, validation, and remaining limitations.

This workspace is configured for local development with HTTP and PHP error display enabled. Database credentials and Redis are shared across projects. Node tooling, HTTPS, application workers, and application-specific migrations are configured separately when needed.
