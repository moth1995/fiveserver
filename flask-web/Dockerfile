FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies
COPY flask-web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY flask-web/ /app/

# Copy config and keys (mounted at runtime or baked in for testing)
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

CMD ["python", "run.py"]
