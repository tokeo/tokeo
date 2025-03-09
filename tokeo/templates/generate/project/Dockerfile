FROM python:3.12-alpine
LABEL MAINTAINER="{{ creator_name }} <{{ creator_email }}>"
ENV PS1="\[\e[0;33m\]\(> {{ app_label }} <\) \[\e[1;35m\]\W\[\e[0m\] \[\e[0m\]# "
ENV {{ app_label | upper }}_ENV=prod

WORKDIR /app
COPY . /app

RUN apk add --update --no-cache \
    tzdata \
{% if feature_grpc == "Y" %}
    linux-headers \
    build-base \
    cmake \
    gcc \
    g++ \
{% endif %}
    git \
    screen \
    && ln -s /usr/share/zoneinfo/UTC /etc/localtime \
    && pip install --upgrade pip \
    && pip install --no-cache-dir -e .

WORKDIR /app
ENTRYPOINT ["{{ app_label }}"]
