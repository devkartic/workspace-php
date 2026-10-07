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
make project-add NAME=myapp PHP=8.4
make project-up NAME=myapp
```

`make setup` checks tools and Docker access, copies `docker/.env.example` only when `docker/.env` is missing, and generates validated configuration. It does not start containers.

Add this entry to your hosts file (`/etc/hosts` on macOS/Linux):

```text
127.0.0.1 myapp.local
```

Open **http://myapp.local**. To check routing without editing your hosts file:

```sh
curl -H 'Host: myapp.local' http://127.0.0.1/
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

Source files and existing application environment files are preserved. Registration supplies infrastructure connections; configure your application's credentials and run its own setup commands as described in [the project guide](PROJECT_GUIDE.md).

## Everyday commands

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
│   ├── project.env               Committed workspace registration
│   ├── .env.example              Safe application environment template
│   ├── dev/php.ini               Optional project PHP settings
│   └── …                         Application source
└── tests/test_workspace.py       Configuration and preservation checks
```

Generated Compose, Nginx, and SQL files are ignored by Git. MariaDB persists in a Docker volume; Redis persists in `docker/data/redis`. Local `.env` files, backups, and IDE settings are also ignored.

## Further reading

- [Project guide](PROJECT_GUIDE.md): configuration, framework setup, daily work, troubleshooting, and data recovery.
- [Code review](docs/CODE_REVIEW.md): reviewed files, resolved issues, validation, and remaining limitations.

This workspace is configured for local development with HTTP and PHP error display enabled. Database credentials and Redis are shared across projects. Node tooling, HTTPS, application workers, and application-specific migrations are configured separately when needed.
