ENV ?= development
export WORKSPACE_ENV := $(ENV)
STACK = python3 scripts/stacks.py
PROJECT_PATH ?= projects/$(NAME)
HOST ?= $(subst _,-,$(NAME)).local
DB ?= $(NAME)
DOCUMENT_ROOT ?= .
SOURCE_ROOT ?= .
HTTP_PORT ?=
DB_ACCESS_PORT ?= 13306
ADMINER_PORT ?= 18080
export DB_ACCESS_PORT ADMINER_PORT

.DEFAULT_GOAL := help
.PHONY: help setup doctor sync project-add project-list project-config project-migrate-config project-up project-down project-build project-restart project-logs project-ps validate up down build restart logs ps shell mariadb mysql redis-cli backup restore gateway-up gateway-sync gateway-down gateway-logs up-all down-all test db-access-enable db-access-disable db-access-status
help:
	@printf '%s\n' \
	  'make setup / doctor                 Initialize/check prerequisites' \
	  'make project-add NAME=api            Create with the tested stable PHP default (optional PHP=8.4)' \
	  'make project-config NAME=api        Scaffold project-owned Docker configuration' \
	  'make project-up NAME=api            Start/reconcile project; build missing runtime images' \
	  'make project-migrate-config NAME=api Move legacy config outside source; enable source mounts' \
	  'make project-down NAME=api          Stop only this project (keep volumes)' \
	  'make project-build NAME=api         Build only this project images' \
	  'make project-restart NAME=api       Restart only this project containers' \
	  'make project-logs / project-ps NAME=api' \
	  'make validate NAME=api              Generate/validate one project including overrides' \
	  'make sync                          Validate all selected projects; start nothing' \
	  'make gateway-up / gateway-sync      Start gateway / gracefully reload routes' \
	  'make gateway-down                  Stop the shared gateway explicitly' \
	  'make up-all / down-all              Explicitly operate on all selected projects' \
	  'make shell / mariadb / redis-cli NAME=api' \
	  'make db-access-enable / db-access-status / db-access-disable NAME=api' \
	  '  Private Sequel Pro/Adminer access; optional DB_ACCESS_PORT=13306 ADMINER_PORT=18080' \
	  'make backup NAME=api                Write a private SQL backup' \
	  'make restore NAME=api FILE=/path/to/backup.sql' \
	  'Append ENV=production for production; see docs/PRODUCTION.md' \
	  'make test                          Run configuration regressions without containers'
setup:
	$(STACK) setup
doctor:
	$(STACK) doctor
sync:
	$(STACK) sync
project-add:
	@test -n "$(NAME)" || (echo 'Usage: make project-add NAME=api [PHP=8.4]'; exit 2)
	$(STACK) add "$(NAME)" "$(PROJECT_PATH)" "$(PHP)" "$(HOST)" "$(DB)" "$(DOCUMENT_ROOT)" "$(HTTP_PORT)" "$(SOURCE_ROOT)"
project-list:
	$(STACK) list
project-config:
	$(STACK) scaffold "$(NAME)"
project-up project-down project-build project-restart project-logs project-ps validate shell mariadb redis-cli backup db-access-enable db-access-disable db-access-status project-migrate-config:
	$(STACK) $@ "$(NAME)"
restore:
	$(STACK) restore "$(NAME)" "$(FILE)"
gateway-up gateway-sync gateway-down gateway-logs up-all down-all:
	$(STACK) $@
# Compatibility aliases still require NAME. No unqualified command operates globally.
up: project-up
down: project-down
build: project-build
restart: project-restart
logs: project-logs
ps: project-ps
mysql: mariadb
test:
	python3 -m unittest discover -s tests -v
