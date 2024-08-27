FROM php:8.2-fpm-alpine3.18
LABEL maintainer = "Jan Rohwer <jan@rohwer.online>"

RUN apk add -U --no-cache bash \
	icu-dev \
	imap-dev \
	openssl \
	openssl-dev \
	c-client \
   && docker-php-ext-configure imap --with-imap --with-imap-ssl \
   && docker-php-ext-install -j 4 imap \
   	&& apk del --purge icu-dev \
     imap-dev \
     openssl-dev

WORKDIR /sabredav
RUN curl -o installer https://getcomposer.org/installer
RUN php installer
RUN php composer.phar require sabre/dav
RUN php composer.phar update sabre/dav
RUN mkdir data
RUN chown www-data:www-data data
RUN mkdir public
RUN chown www-data:www-data public

WORKDIR /
COPY ./docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["php-fpm"]
