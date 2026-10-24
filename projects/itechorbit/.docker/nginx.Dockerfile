FROM nginx:stable-alpine AS base
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
FROM base AS development
LABEL io.php-workspace.runtime=mounted-development
FROM base AS production
LABEL io.php-workspace.runtime=mounted-production
