# PHP development stack

This workspace runs any number of PHP applications behind Nginx. Each project independently selects PHP-FPM 7.4, 8.1, 8.3, or 8.4, while sharing MySQL 8.4 and Redis 7.

## Quick start

```sh
cp docker/.env.example docker/.env
make up
```

## Add a project

From the workspace root, create the project directory and run this command:

```sh
mkdir -p projects/api
make project-add NAME=api PROJECT_PATH=projects/api PHP=8.3 HOST=api.local DB=api
```

This creates `projects/api/.env`. Then add `127.0.0.1 api.local` to your hosts file and run `make up`. The shared MySQL server creates the `api` database automatically.

The included sample project is available at:

```text
http://dashboard.local
```

Useful commands: `make project-list`, `make up`, `make down`, `make logs`, `make shell PROJECT=api`, `make mysql`, and `make redis-cli`. Redis is exposed on host port `6380` by default (container port `6379`); change `REDIS_PORT` in `docker/.env` if needed. `make down` keeps MySQL and Redis data.

## Project settings

Every project uses shared settings in `docker/php/php.ini`. To override them, create `projects/<project>/dev/php.ini`, then run `make sync` or `make up`; it is mounted last as `zz-project.ini`.

Project definitions live at the project root in `projects/<project>/.env`. They are the reusable source of truth and are safe to edit directly. Run `make sync` after editing one to regenerate the ignored Compose, Nginx, and database setup files.

PHP errors are saved to `projects/<project>/dev/logs/php-error.log` when the project's `dev/php.ini` sets `log_errors = On` and an `error_log` path, as the sample dashboard does.

The existing MariaDB files in `docker/data/mariadb` are deliberately not mounted by MySQL because their on-disk formats are incompatible. They remain untouched; use a logical dump to migrate any data you need.
