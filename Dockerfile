FROM python:3.12-alpine
LABEL MAINTAINER="Tom Freudenberg <th.freudenberg@gmail.com>"
ENV PS1="\[\e[0;33m\]\(> {{ app_label }} <\) \[\e[1;35m\]\W\[\e[0m\] \[\e[0m\]# "
ENV TOKEO_ENV=prod

WORKDIR /app
COPY . /app

RUN apk add --update --no-cache \
    tzdata \
    git \
    && ln -s /usr/share/zoneinfo/UTC /etc/localtime \
    && pip install --upgrade pip \
    && pip install --no-cache-dir -e .

WORKDIR /app
ENTRYPOINT ["tokeo"]
