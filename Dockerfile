# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.13

# Build dependencies and wheels in a throwaway stage.
FROM python:${PYTHON_VERSION}-alpine AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# asyncmy has no cp313 wheels, so it is compiled here. Nothing from this stage
# (compiler, headers, build cache) is copied to the final image.
RUN apk add --no-cache build-base

COPY pyproject.toml ./
COPY src ./src
RUN pip install --prefix=/install .

# Runtime stage: only Python, the installed packages, and runtime files.
FROM python:${PYTHON_VERSION}-alpine AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ca-certificates: outbound HTTPS for IpDetectUri; tzdata: LogTimezone zones.
RUN apk add --no-cache ca-certificates tzdata libgcc

# Copy the whole prefix rather than a versioned site-packages path. This keeps
# the runtime stage correct when PYTHON_VERSION changes.
COPY --from=builder /install/ /usr/local/
COPY web ./web
COPY web6 ./web6
COPY sql ./sql

RUN mkdir -p /app/etc/conf /app/etc/data

CMD ["python", "-m", "fiveserver", "five"]
