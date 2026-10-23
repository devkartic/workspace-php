# This file belongs to this project. Add extensions/packages here independently.
ARG PHP_VERSION=8.5
FROM php:${PHP_VERSION}-fpm AS base
# PHP 8.5 includes OPcache; compile it only when absent on older branches.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl default-mysql-client libfcgi-bin git libfreetype6-dev libicu-dev \
    libjpeg62-turbo-dev libonig-dev libpng-dev libxml2-dev libzip-dev unzip zip \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" exif gd intl mbstring mysqli pdo pdo_mysql zip \
    && if ! php -r 'exit(extension_loaded("Zend OPcache") ? 0 : 1);'; then docker-php-ext-install -j"$(nproc)" opcache; fi \
    && rm -rf /var/lib/apt/lists/*
COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
COPY .docker/php.ini /usr/local/etc/php/conf.d/zz-custom.ini
COPY .docker/fpm.conf /usr/local/etc/php-fpm.d/zz-project.conf
COPY .docker/prepare-logs /usr/local/bin/prepare-logs
COPY .docker/fpm-healthcheck /usr/local/bin/fpm-healthcheck
RUN chmod 755 /usr/local/bin/fpm-healthcheck
WORKDIR /var/www/html
FROM base AS development
FROM base AS production
COPY --chown=www-data:www-data . /var/www/html
COPY .docker/production.ini /usr/local/etc/php/conf.d/zzz-production.ini
COPY .docker/production-fpm.conf /usr/local/etc/php-fpm.d/zzz-production.conf
