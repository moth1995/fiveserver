"""
Self-signed TLS certificate for the PES2009 web login (auth.php over https).

    python scripts/make_pes2009_cert.py [extra hostnames or IPs...]

Covers pes2009web.winning-eleven.net, 127.0.0.1 and the extra names: add the
host of nineserver.yaml WebBaseUrl (e.g. the server's public IP).

Writes etc/keys/pes2009web-key.pem and etc/keys/pes2009web-cert.pem.
The game uses Windows' HTTPS stack (WinINet), which rejects untrusted certs,
so every player PC must trust it once (admin PowerShell):

    certutil -addstore -f Root etc\\keys\\pes2009web-cert.pem
"""

import datetime
import ipaddress
import os
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

HOSTS = ["pes2009web.winning-eleven.net", "127.0.0.1"] + sys.argv[1:]
KEYS = os.path.join(os.path.dirname(__file__), "..", "etc", "keys")



def san(host):
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])])
now = datetime.datetime.utcnow()
cert = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=1))
    .not_valid_after(now + datetime.timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName([san(h) for h in HOSTS]), False)
    # self-signed leaf that is also its own CA, so it can sit in the Root store
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    .add_extension(
        x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False)
    .sign(key, hashes.SHA256())
)

keyPath = os.path.join(KEYS, "pes2009web-key.pem")
certPath = os.path.join(KEYS, "pes2009web-cert.pem")
with open(keyPath, "wb") as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
with open(certPath, "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("wrote %s and %s for %s" % (keyPath, certPath, ", ".join(HOSTS)))
print("trust it (admin): certutil -addstore -f Root %s" % os.path.normpath(certPath))
