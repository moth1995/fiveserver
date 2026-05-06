FROM python:3.12-alpine AS builder

RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev
COPY flask-web/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

FROM python:3.12-alpine

COPY --from=builder /install /usr/local
WORKDIR /app
COPY flask-web/ /app/flask-web/
COPY etc/ /app/etc/

EXPOSE 80 443

# Environment variable overrides (all optional):
#   HTTP_PORT       plain-HTTP port      (default from fiveserver.yaml → 80)
#   HTTPS_PORT      HTTPS port           (default from fiveserver.yaml → 443)
#   CERT_FILE       TLS cert path        (default etc/keys/servercert.pem)
#   KEY_FILE        TLS key path         (default etc/keys/serverkey.pem)
#   FLASK_SECRET    Flask secret key     (default: random on startup)
#   DB_HOST         MySQL host           (default from fiveserver.yaml → 127.0.0.1)
#   DB_PORT         MySQL port           (default from fiveserver.yaml → 3306)
#   DB_NAME         MySQL database name  (default from fiveserver.yaml)
#   DB_USER         MySQL user           (default from fiveserver.yaml)
#   DB_PASSWORD     MySQL password       (default from fiveserver.yaml)

CMD ["python", "flask-web/run.py"]
