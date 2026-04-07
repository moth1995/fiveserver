FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies
COPY flask-web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY flask-web/ /app/

# Copy config and keys (mounted at runtime or baked in for testing)
COPY etc/ /app/../etc/

EXPOSE 80 443

# Environment variable overrides (all optional):
#   HTTP_PORT       plain-HTTP port      (default 80)
#   HTTPS_PORT      HTTPS port           (default 443)
#   CERT_FILE       TLS cert path        (default etc/keys/servercert.pem)
#   KEY_FILE        TLS key path         (default etc/keys/serverkey.pem)
#   ADMIN_USER      admin username       (default from admin.yaml)
#   ADMIN_PASSWORD  admin password       (default from admin.yaml)
#   FLASK_SECRET    Flask secret key     (default: random on startup)
#   DB_HOST         MySQL host override
#   DB_USER         MySQL user override
#   DB_PASSWORD     MySQL password override

CMD ["python", "run.py"]
