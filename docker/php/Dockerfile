ARG PHP_VERSION=8.3

FROM php:${PHP_VERSION}-apache

RUN apt-get update && apt-get install -y \
    git \
    curl \
    unzip \
    zip \
    vim \
    libzip-dev \
    libicu-dev \
    libpng-dev \
    libjpeg62-turbo-dev \
    libfreetype6-dev \
    libonig-dev \
    libxml2-dev \
    default-mysql-client \
    && rm -rf /var/lib/apt/lists/*

RUN docker-php-ext-configure gd \
    --with-freetype \
    --with-jpeg

RUN docker-php-ext-install \
    mysqli \
    pdo \
    pdo_mysql \
    mbstring \
    intl \
    zip \
    exif \
    gd \
    opcache

RUN a2enmod rewrite headers expires

COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer

COPY php.ini /usr/local/etc/php/conf.d/custom.ini

WORKDIR /var/www/html