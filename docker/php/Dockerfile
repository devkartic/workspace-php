ARG PHP_VERSION=8.3
# One build recipe supports every project; the project definition selects the version.
FROM php:${PHP_VERSION}-fpm

# Install application extensions together to reuse cached layers for the same PHP version.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl default-mysql-client git libfreetype6-dev libicu-dev \
    libjpeg62-turbo-dev libonig-dev libpng-dev libxml2-dev libzip-dev \
    unzip zip \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" \
      exif gd intl mbstring mysqli opcache pdo pdo_mysql zip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
# Project overrides are mounted as zz-project.ini, after these shared defaults.
COPY php.ini /usr/local/etc/php/conf.d/zz-custom.ini

WORKDIR /var/www/html
