from pathlib import Path
from unittest.mock import Mock

import pytest

from config.settings import Settings
from services.efi_api_client import EfiConfigurationError, EfiOpenFinanceClient


def settings(**overrides):
    opts = {
        "ADMIN_API_KEY": "admin-test-key-000000000000000000000000000000",
        "EFI_WEBHOOK_HASH": "webhook-test-hash-00000000000000000000000000",
        "EFI_ENVIRONMENT": "homologation",
        **overrides,
    }
    return Settings(**opts)


def test_url_and_credentials_validation():
    config = settings()
    assert config.efi_api_base == "https://openfinance-h.api.efipay.com.br"
    api = EfiOpenFinanceClient(config)
    with pytest.raises(EfiConfigurationError):
        api._credentials()
    with pytest.raises(EfiConfigurationError):
        api._ssl_context()
    with pytest.raises(ValueError):
        api.configure_urls("http://localhost/redirect", "https://example.com/hook", "secret")


def test_missing_certificate_file():
    api = EfiOpenFinanceClient(settings(efi_certificate_path=Path("/does/not/exist.p12")))
    with pytest.raises(EfiConfigurationError):
        api._ssl_context()


def test_oauth_token_error_handling():
    api = EfiOpenFinanceClient(settings(efi_client_id="fake", efi_client_secret="fake"))
    fake_client = Mock()
    fake_response = Mock()
    fake_response.json.return_value = {"access_token": "example"}
    fake_client.post.return_value = fake_response
    assert api._token(fake_client) == "example"
    fake_response.json.return_value = {}
    with pytest.raises(EfiConfigurationError):
        api._token(fake_client)



def test_loads_p12_certificate(tmp_path):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    p12 = tmp_path / "homologation.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(
        b"teste", key, cert, None, serialization.NoEncryption()
    ))
    client = EfiOpenFinanceClient(settings(efi_certificate_path=p12))
    assert client._ssl_context() is not None
