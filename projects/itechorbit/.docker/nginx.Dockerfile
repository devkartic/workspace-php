FROM nginx:stable-alpine AS base
COPY .docker/nginx.conf.template /etc/nginx/templates/default.conf.template
FROM base AS development
FROM base AS production
ARG DOCUMENT_ROOT=public
COPY ${DOCUMENT_ROOT}/ /var/www/html/${DOCUMENT_ROOT}/
