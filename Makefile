ENV_FILE := $(if $(wildcard docker/.env),docker/.env,docker/.env.example)
PROJECTS := docker/generated/projects.compose.yml
COMPOSE = docker compose --env-file $(ENV_FILE) -f docker/docker-compose.yml -f $(PROJECTS)

.DEFAULT_GOAL := help
.PHONY: help sync project-add project-list up down restart build ps logs validate tools shell mysql redis-cli

help:
	@printf '%s\n' \
	  'make sync          Regenerate Compose, Nginx, and database setup from project definitions' \
	  'make project-add NAME=api PROJECT_PATH=projects/api PHP=8.3 HOST=api.local DB=api' \
	  'make project-list  List registered projects, PHP versions, hosts, and databases' \
	  'make up            Start shared services and all registered projects' \
	  'make down          Stop the stack (keeps database and Redis data)' \
	  'make build         Rebuild all PHP images' \
	  'make logs          Follow all service logs' \
	  'make ps            Show service status' \
	  'make validate      Validate the Compose configuration' \
	  'make tools         Start optional phpMyAdmin at :8081' \
	  'make shell PROJECT=dashboard  Open a project container shell' \
	  'make mysql         Open a MySQL root shell' \
	  'make redis-cli     Open Redis CLI'

sync:
	./scripts/projects sync

project-add:
	@test -n "$(NAME)" -a -n "$(PROJECT_PATH)" -a -n "$(PHP)" -a -n "$(HOST)" -a -n "$(DB)" || (echo 'Usage: make project-add NAME=api PROJECT_PATH=projects/api PHP=8.3 HOST=api.local DB=api'; exit 2)
	./scripts/projects add "$(NAME)" "$(PROJECT_PATH)" "$(PHP)" "$(HOST)" "$(DB)"

project-list:
	./scripts/projects list

up:
	./scripts/projects sync
	$(COMPOSE) up -d --build --remove-orphans
	$(COMPOSE) exec nginx nginx -s reload
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
tools:
	$(COMPOSE) --profile tools up -d phpmyadmin
shell:
	@test -n "$(PROJECT)" || (echo 'Usage: make shell PROJECT=dashboard'; exit 2)
	$(COMPOSE) exec $(PROJECT) sh
mysql:
	$(COMPOSE) exec mysql mysql -uroot -p$${MYSQL_ROOT_PASSWORD:-root}
redis-cli:
	$(COMPOSE) exec redis redis-cli
