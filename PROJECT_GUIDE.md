# Developer Project Guide

This guide explains how to initialize the workspace, create or register an application, and work with its runtime. Run host commands from the repository root unless a step says to run inside a container.

## 1. Initialize your environment

Install the requirements listed in [README.md](README.md), start Docker, and run:

```sh
make setup
```

Setup checks Docker, Compose, Make, and daemon access. It preserves an existing `docker/.env`, creates one from the example when missing, and generates configuration from every registration under `projects/*/project.env`. Missing application `.env.example` and `.env` files are also created; existing ones are preserved.

Review `docker/.env` before starting containers:

| Variable | Default | Purpose |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | `php-workspace` | Compose stack, network, and volume prefix |
| `NGINX_HTTP_PORT` | `80` | Host HTTP port |
| `MARIADB_PORT` | `3306` | Host MariaDB port |
| `REDIS_PORT` | `6380` | Host Redis port; containers use `6379` |
| `PHPMYADMIN_PORT` | `8081` | Optional browser database tool |
| `MARIADB_ROOT_PASSWORD` | `root` | Shared development root credential |

Keep credentials in local `.env` files. Changing `MARIADB_ROOT_PASSWORD` in this file does not change an account in an already initialized database volume. Update that account deliberately through MariaDB and then update local application settings.

Keep `COMPOSE_PROJECT_NAME` stable once you have data: changing it selects a different named MariaDB volume. If running separate workspace clones, use unique stack names and host ports. Docker must be allowed to mount any external source directory you register.

## 2. Create or register a project

Supported versions in the generator: **7.4, 8.1, 8.3, and 8.4**. Choose the version required by the application and its Composer dependencies.

```sh
make project-add NAME=myapp PHP=8.4
```

Names and database names must start with a lowercase letter and contain only lowercase letters, numbers, or underscores. Shared service names such as `nginx`, `mariadb`, and `redis` are reserved. Underscores become hyphens in the default hostname: `my_app` uses `my-app.local`.

| Registration field | Default for `myapp` | Meaning |
| --- | --- | --- |
| `PROJECT_NAME` | `myapp` | PHP Compose service name |
| `PROJECT_PATH` | `projects/myapp` | Source path, relative to workspace root or absolute |
| `PHP_VERSION` | Required | PHP-FPM image version |
| `HOSTNAME` | `myapp.local` | Unique Nginx hostname |
| `DATABASE` | `myapp` | Database to create on the shared MariaDB server |
| `DOCUMENT_ROOT` | `.` | Web directory relative to source root; commonly `public` |

A new default project receives a small runtime-check endpoint and application environment templates. To create a starter with a public web directory:

```sh
make project-add NAME=myapp PHP=8.4 DOCUMENT_ROOT=public
```

For an existing application:

```sh
make project-add NAME=api PHP=8.4 \
  PROJECT_PATH=/absolute/path/to/api \
  DOCUMENT_ROOT=public HOST=api.local DB=api
```

Custom source paths must already exist, and an existing application's document root must exist. Quote paths containing spaces. Registration lives under `projects/*/project.env`; external and nested applications have a registration directory in the workspace. For new projects, the generator creates the chosen web directory. `DOCUMENT_ROOT` accepts `.` or relative segments such as `public` and `web/public`; absolute paths and traversal are rejected.

Example registration:

```sh
PROJECT_NAME=api
PROJECT_PATH='/absolute/path/to/api'
PHP_VERSION=8.4
HOSTNAME=api.local
DATABASE=api
DOCUMENT_ROOT=public
```

`project.env` is trusted shell configuration sourced by the generator. Keep it to the documented assignments and review it as code. Store application settings in the application's `.env`.

Use the framework's public directory when it has one. With `DOCUMENT_ROOT=.`, non-hidden source files are within the web root. Generated Nginx configuration denies hidden paths and `project.env`; it does not classify every application file as private.

## 3. Configure application connections

Containers use these addresses regardless of host port overrides:

| Setting | Value |
| --- | --- |
| Database host / port | `mariadb` / `3306` |
| Database name | Project's `DATABASE` |
| Database username | `root` by default |
| Database password | `MARIADB_ROOT_PASSWORD` from `docker/.env` |
| Redis host / port | `redis` / `6379` |

The generated application template includes `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`, `REDIS_HOST`, and `REDIS_PORT`. The password is intentionally blank in the template. Set it in the actual source directory's local `.env` and adapt variable names to your framework.

PHP containers also receive the database host/port/name and Redis host/port as process environment variables. Credentials are not injected. Frameworks differ in how they combine process variables and dotenv files; keep registration and application settings consistent, and clear cached configuration when your framework requires it.

Plain PHP does not load `.env` files automatically. Use the application's environment loader or explicitly configured process settings. The installed extensions include `mysqli` and `pdo_mysql`; a PHP Redis extension is not installed, so use an application-compatible client library or add the required extension to the image.

MariaDB is shared and uses one root account; a separate database does not provide permission isolation. Redis is shared too: configure distinct application key prefixes or logical databases as appropriate.

## 4. Start and verify

```sh
make project-up NAME=api
```

This regenerates validated configuration, builds the selected PHP runtime, starts it and the shared services, reloads Nginx, and creates any missing project databases. Other PHP projects are not selected for rebuilding. Initial builds can take several minutes; subsequent builds reuse Docker's cache.

Add the hostname to your hosts file:

```text
127.0.0.1 api.local
```

Open `http://api.local`, or check routing directly:

```sh
curl -H 'Host: api.local' http://127.0.0.1/
```

If `NGINX_HTTP_PORT=8090`, use `http://api.local:8090` or `http://127.0.0.1:8090/`.

Check the running runtime:

```sh
make shell PROJECT=api
# Inside the PHP container:
php -v
php --ini
php -r 'echo ini_get("memory_limit"), PHP_EOL;'
composer --version
exit
```

A pre-existing application requires its own installation steps after infrastructure starts. Within its container, run `composer install`, create its application key if required, and run its documented migrations. If you register an empty existing directory, the generator preserves it rather than inserting a starter endpoint.

## 5. Daily development

Edit source on the host; bind mounts make changes available in the container. Run PHP and Composer commands inside the selected container to use the application's actual runtime and extensions:

```sh
make shell PROJECT=api
composer install
# Run your application's CLI and test commands here.
exit
make ps
make logs
```

The image includes Composer, curl, Git, zip/unzip, and a database client. It does not include Node/npm, a queue worker, or a scheduler. Applications requiring frontend assets or background jobs need their own documented tools/services.

`make up` starts all registered projects. `make down` stops the stack and retains persistent data. `make restart` restarts existing containers; it does not apply new image builds or volume mounts. `make build` builds PHP images but does not recreate running containers.

To change PHP versions, edit `PHP_VERSION` in the registration and run `make project-up NAME=api`. Recheck Composer dependencies for the new runtime.

To change a hostname, source path, database, or document root, edit the registration and run `make project-up NAME=api`. Update hosts/application settings as needed. Renaming `DATABASE` creates a new database; it does not move existing data.

## 6. Customize PHP and system packages

Shared settings live in `docker/php/php.ini`: memory `512M`, upload and request limits `128M`, execution time `300` seconds, error display on, and timezone `Asia/Dhaka`.

For project-only overrides, create `dev/php.ini` inside the actual application source directory:

```ini
memory_limit = 1024M
upload_max_filesize = 256M
post_max_size = 256M
log_errors = On
error_log = /proc/self/fd/2
```

Then run:

```sh
make project-restart NAME=api
```

The project file mounts as `zz-project.ini`, after shared `zz-custom.ini`. Recreating the container picks up new mounts and settings. `make sync` alone does not apply them to a running PHP process.

Nginx also imposes a request-body limit, configured to `128m` to match the shared PHP defaults. For larger uploads, change `client_max_body_size` in `docker/nginx/base.conf` along with PHP upload/request limits and apply it with `make up`.

Add permanent system packages or PHP extensions in `docker/php/Dockerfile`, then rebuild with `make project-up NAME=api`. The recipe is shared across PHP versions, so verify compatibility with affected versions. Manual installations in a running container disappear on recreation.

## 7. Database tools and persistence

```sh
make tools       # phpMyAdmin; default http://localhost:8081
make mariadb     # MariaDB root CLI
make redis-cli   # Redis CLI
```

`make mysql` is an alias for `make mariadb`. Desktop database clients connect to `127.0.0.1`, the configured `MARIADB_PORT`, username `root`, and the local root password. Container applications use `mariadb:3306` instead.

MariaDB persists in the Compose-managed `mariadb_data` volume, normally named `php-workspace_mariadb_data`. Redis AOF files persist in `docker/data/redis`. Neither is source code. `docker/backups/` is ignored and can hold your logical SQL backups. `make down` retains these stores; Docker volume deletion and `down -v` can remove MariaDB data.

Back up important databases with a logical SQL export using your database client or phpMyAdmin, and verify a restore into a separate database before deleting old data. Do not reuse MySQL's raw storage files as MariaDB data. Historical migration details and backups are local to the original workstation and are not prerequisites for a fresh clone.

To unregister a project, remove its registration file after saving any needed configuration, then run `make up`. This removes stale Nginx configuration and orphan containers; source files and databases are not deleted. Avoid changing a project name casually because it identifies its runtime service.

## 8. Troubleshoot and contribute

| Symptom | Check or action |
| --- | --- |
| Docker connection failure | Run `make doctor`; start Docker and check socket access |
| Host port already in use | Change the corresponding port in `docker/.env`, then `make up` |
| Hostname does not resolve | Add the hosts entry, or test with curl's `Host` header |
| HTTP 404 | Check hostname, `DOCUMENT_ROOT`, and the actual entry-point file |
| HTTP 502 | Check `make ps` and PHP container logs; start the selected project |
| Database access denied | Check the application password and the account in the existing volume |
| PHP settings unchanged | Run `make project-restart NAME=api`, then inspect `php --ini` |
| Upload returns HTTP 413 | Set Nginx `client_max_body_size` as well as PHP upload/request limits |
| Mounted directory is unavailable | Verify the path and Docker Desktop file-sharing permissions |

Before submitting workspace changes:

```sh
make validate
make test
```

Tests use temporary workspace copies and Compose configuration validation. They do not start containers or modify your real project data. They require Python 3, a local PHP CLI, and Compose. Application tests remain the responsibility of each project.

Edit registration files and source configuration, then regenerate. Do not edit `docker/generated/` or `docker/nginx/generated/` directly. Generated output is validated with Compose before replacing live files; Nginx syntax and runtime behavior need separate verification when changing routing.

Nginx reads application files at `/var/www/projects/<name>` and PHP-FPM reads them at `/var/www/html`. The generator maps script paths and document roots between those mounts. Use `__DIR__` for PHP includes so they resolve relative to the executing file.

Commit safe templates, registrations, and application lockfiles. Do not commit `.env`, generated configuration, database files, logs, or dependencies. Consult [the code review](docs/CODE_REVIEW.md) for known limitations and verification scope.

## Reusable project request

```text
Read README.md and PROJECT_GUIDE.md, then create or register a project:
Name: myapp
PHP: 8.4
Source path: projects/myapp
Document root: public
Hostname: myapp.local
Database: myapp
Optional PHP settings:
  memory_limit = 1024M
  upload_max_filesize = 256M
  post_max_size = 256M
Preserve existing application files and database data. Validate the configuration,
start the requested project when Docker is available, and verify its actual PHP
version and requested ini values. Report the URL and hosts entry needed.
```
