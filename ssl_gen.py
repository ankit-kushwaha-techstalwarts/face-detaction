"""
FaceAttend — Self-Signed SSL Certificate Generator
Generates cert.pem + key.pem for HTTPS.

Requirements: pip install cryptography
"""

import os, socket, ipaddress, datetime
from pathlib import Path

CERT_FILE = Path(__file__).parent / 'cert.pem'
KEY_FILE  = Path(__file__).parent / 'key.pem'
VALIDITY_DAYS = 3650   # 10 years


def get_local_ips():
    """Collect all local IP addresses on this machine."""
    ips = {'127.0.0.1', '0.0.0.0'}
    try:
        hostname = socket.gethostname()
        ips.add(socket.gethostbyname(hostname))
    except Exception:
        pass
    # Try all network interfaces
    try:
        import subprocess, platform
        if platform.system() == 'Windows':
            out = subprocess.check_output('ipconfig', text=True)
            import re
            for ip in re.findall(r'IPv4 Address[.\s]+:\s*([\d.]+)', out):
                ips.add(ip)
        else:
            out = subprocess.check_output(['hostname', '-I'], text=True)
            for ip in out.split():
                ips.add(ip.strip())
    except Exception:
        pass
    return ips


def generate_certificate():
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    print("[SSL] Generating RSA 2048 private key…")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Subject / Issuer
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME,             "IN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME,   "India"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,        "FaceAttend Govt Dept"),
        x509.NameAttribute(NameOID.COMMON_NAME,              "faceattend.local"),
    ])

    # Subject Alternative Names — add all local IPs + common hostnames
    local_ips = get_local_ips()
    san_ips   = []
    san_dns   = [
        x509.DNSName("localhost"),
        x509.DNSName("faceattend.local"),
        x509.DNSName(socket.gethostname()),
    ]
    for ip in local_ips:
        try:
            san_ips.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass

    print(f"[SSL] Certificate will be valid for IPs: {sorted(str(i.value) for i in san_ips)}")

    now  = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(san_dns + san_ips),
            critical=False
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False
            ),
            critical=True
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,
                ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False
        )
        .sign(key, hashes.SHA256())
    )

    # Write key
    KEY_FILE.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        )
    )
    # Write cert
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"[SSL] Certificate saved → {CERT_FILE}")
    print(f"[SSL] Private key saved  → {KEY_FILE}")
    print(f"[SSL] Valid for {VALIDITY_DAYS} days ({VALIDITY_DAYS//365} years)")
    return str(CERT_FILE), str(KEY_FILE)


def ensure_certificates():
    """Generate only if cert/key don't exist yet."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        print("[SSL] Using existing certificate.")
        return str(CERT_FILE), str(KEY_FILE)
    return generate_certificate()


if __name__ == '__main__':
    try:
        generate_certificate()
        print("\n[SSL] Done! Run the app with: python app.py")
    except ImportError:
        print("[SSL] ERROR: cryptography package not found.")
        print("       Run: pip install cryptography")
        raise
