FROM python:3.12-alpine
LABEL MAINTAINER="{{ author_name }} <{{ author_email }}>"
ENV PS1="\[\e[0;33m\]\(> {{ app_label }} <\) \[\e[1;35m\]\W\[\e[0m\] \[\e[0m\]# "

WORKDIR /app
COPY . /app
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -e .
WORKDIR /
ENTRYPOINT ["{{ app_label }}"]
