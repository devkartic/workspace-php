ENV_FILE := $(if $(wildcard docker/.env),docker/.env,docker/.env.example)
PROJECTS := docker/generated/projects.compose.yml
COMPOSE = docker compose --env-file $(ENV_FILE) -f docker/docker-compose.yml -f $(PROJECTS)
# Only name and PHP are required; these defaults keep project creation consistent.
PROJECT_PATH ?= projects/$(NAME)
HOST ?= $(subst _,-,$(NAME)).local
DB ?= $(NAME)
DOCUMENT_ROOT ?= .

.DEFAULT_GOAL := help
.PHONY: help setup doctor sync project-add project-list project-up project-restart up down restart build ps logs validate tools shell mariadb mysql redis-cli test

help:
	@printf '%s\n' \
	  'make setup         Check prerequisites, create local Docker env, and generate configuration' \
	  'make doctor        Check required tools and Docker daemon access' \
	  'make sync          Regenerate Compose, Nginx, and database setup from project definitions' \
	  'make project-add NAME=api PHP=8.4  Create a project (optional PROJECT_PATH, HOST, DB, DOCUMENT_ROOT)' \
	  'make project-list  List registered projects, PHP versions, hosts, and databases' \
	  'make project-up NAME=api  Start or rebuild one project and shared services' \
	  'make project-restart NAME=api  Apply PHP ini changes to one project' \
	  'make up            Start shared services and all registered projects' \
	  'make down          Stop the stack (keeps database and Redis data)' \
	  'make build         Rebuild all PHP images' \
	  'make logs          Follow all service logs' \
	  'make ps            Show service status' \
	  'make validate      Validate the Compose configuration' \
	  'make tools         Start optional phpMyAdmin at :8081' \
	  'make shell PROJECT=dashboard  Open a project container shell' \
	  'make mariadb       Open a MariaDB root shell (make mysql is an alias)' \
	  'make redis-cli     Open Redis CLI' \
	  'make test          Run isolated regression checks'

setup:
	./scripts/workspace setup

doctor:
	./scripts/workspace doctor

sync:
	./scripts/projects sync

project-add:
	@test -n "$(NAME)" -a -n "$(PHP)" || (echo 'Usage: make project-add NAME=api PHP=8.4'; exit 2)
	./scripts/projects add "$(NAME)" "$(PROJECT_PATH)" "$(PHP)" "$(HOST)" "$(DB)" "$(DOCUMENT_ROOT)"

project-list:
	./scripts/projects list

# Start only the selected PHP runtime; other projects keep their own versions.
project-up:
	./scripts/projects check "$(NAME)"
	./scripts/projects sync
	$(COMPOSE) up -d --build $(NAME) nginx mariadb redis
	$(COMPOSE) exec -T nginx nginx -s reload
	$(COMPOSE) --profile init run --rm database-init

# Recreate to pick up a newly added ini mount as well as edits to an existing one.
project-restart:
	./scripts/projects check "$(NAME)"
	./scripts/projects sync
	$(COMPOSE) up -d --build --no-deps --force-recreate $(NAME)

up:
	./scripts/projects sync
	$(COMPOSE) up -d --build --remove-orphans
	$(COMPOSE) exec -T nginx nginx -s reload
	$(COMPOSE) --profile init run --rm database-init

down:
	$(COMPOSE) down
restart:
	$(COMPOSE) restart
build:
	./scripts/projects sync
	$(COMPOSE) build
ps:
	$(COMPOSE) ps
logs:
	$(COMPOSE) logs -f
validate:
	./scripts/projects sync
	$(COMPOSE) config --quiet
tools: sync
	$(COMPOSE) --profile tools up -d phpmyadmin
shell:
	@test -n "$(PROJECT)" || (echo 'Usage: make shell PROJECT=dashboard'; exit 2)
	$(COMPOSE) exec $(PROJECT) sh
mariadb:
	$(COMPOSE) exec mariadb sh -c 'MYSQL_PWD="$$MARIADB_ROOT_PASSWORD" exec mariadb -uroot'

# Keep the old shortcut usable while the server and actual CLI are MariaDB.
mysql: mariadb
redis-cli:
	$(COMPOSE) exec redis redis-cli

test:
	python3 -m unittest discover -s tests -v
