FROM python:3.12-alpine AS builder

LABEL maintainer="Anton Jouline <juce66@gmail.com>"

ENV FSROOT=/opt/fiveserver \
    FSENV=/usr/local \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR ${FSROOT}

RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    mariadb-connector-c-dev

COPY pip.requirements ./

RUN pip install --upgrade pip wheel \
    && pip install -r ./pip.requirements

FROM python:3.12-alpine

LABEL maintainer="Anton Jouline <juce66@gmail.com>"

ENV FSROOT=/opt/fiveserver \
    FSENV=/usr/local \
    SVC=fiveserver \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR ${FSROOT}

RUN apk add --no-cache \
    bash \
    mariadb-connector-c

COPY --from=builder /usr/local /usr/local

COPY etc ./etc
COPY lib ./lib
COPY log ./log
COPY sql ./sql
COPY tac ./tac
COPY web ./web
COPY web6 ./web6
COPY docker ./docker
COPY service.sh ./

RUN chmod +x ./service.sh

CMD ["/bin/sh", "-c", "exec \"$FSROOT/service.sh\" \"$SVC\" runexec"]
