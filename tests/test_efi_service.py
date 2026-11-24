from pathlib import Path
from unittest.mock import Mock

import pytest

from config.settings import Settings
from services.efi_api_client import EfiConfigurationError, EfiOpenFinanceClient


def settings(**overrides):
    opts = {
        "admin_api_key": "admin-test-key-000000000000000000000000000000",
        "efi_webhook_hash": "webhook-test-hash-00000000000000000000000000",
        "efi_environment": "homologation",
        "efi_client_id": None,
        "efi_client_secret": None,
        "efi_certificate_path": None,
        "efi_certificate_password": None,
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


def test_start_pix_homologation(monkeypatch):
    api = EfiOpenFinanceClient(settings())

    payload = {
        "pagador": {"idParticipante": "participante-teste"},
        "favorecido": {"chave": "chave-teste"},
        "pagamento": {"valor": "1.00"},
    }

    chamadas = []

    def fake_request(method, endpoint, body=None, *, idempotency_key=None):
        chamadas.append((method, endpoint, body, idempotency_key))
        return {
            "identificadorPagamento": "urn:efi:teste",
            "redirectURI": "https://example.com/autorizar",
        }

    monkeypatch.setattr(api, "_request", fake_request)

    resultado = api.start_pix(payload, idempotency_key=("a" * 72))

    assert resultado["identificadorPagamento"] == "urn:efi:teste"
    assert chamadas == [
        ("POST", "/v1/pagamentos/pix", payload, ("a" * 72))
    ]


def test_start_pix_blocks_production(monkeypatch):
    api = EfiOpenFinanceClient(
        settings(efi_environment="production")
    )

    def forbidden_request(*args, **kwargs):
        raise AssertionError("Não deveria acessar a API")

    monkeypatch.setattr(api, "_request", forbidden_request)

    with pytest.raises(
        EfiConfigurationError,
        match="bloqueada fora de Homologação",
    ):
        api.start_pix({
            "pagador": {},
            "favorecido": {},
            "pagamento": {},
        }, idempotency_key="pix-producao-001")


def test_start_pix_requires_payload():
    api = EfiOpenFinanceClient(settings())

    with pytest.raises(ValueError, match="Payload Pix inválido"):
        api.start_pix({"pagador": {}}, idempotency_key=("a" * 72))


def test_start_pix_rejects_invalid_idempotency_key():
    api = EfiOpenFinanceClient(settings())

    payload = {
        "pagador": {},
        "favorecido": {},
        "pagamento": {},
    }

    for key in ("", "pix com espacos", "a" * 129):
        with pytest.raises(ValueError, match="idempotência"):
            api.start_pix(payload, idempotency_key=key)


def test_start_pix_sends_idempotency_header(monkeypatch):
    from unittest.mock import MagicMock

    api = EfiOpenFinanceClient(settings())

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.request.return_value.json.return_value = {
        "identificadorPagamento": "urn:efi:teste",
        "redirectURI": "https://example.com/autorizar",
    }

    monkeypatch.setattr(
        "services.efi_api_client.httpx.Client",
        lambda **kwargs: fake_client,
    )

    monkeypatch.setattr(api, "_ssl_context", lambda: True)
    monkeypatch.setattr(api, "_token", lambda client: "token-falso")

    payload = {
        "pagador": {"idParticipante": "teste"},
        "favorecido": {"chave": "chave-teste"},
        "pagamento": {"valor": "1.00"},
    }

    api.start_pix(
        payload,
        idempotency_key=("a" * 72),
    )

    args, kwargs = fake_client.request.call_args

    assert args == ("POST", "/v1/pagamentos/pix")
    assert kwargs["json"] == payload
    assert kwargs["headers"]["x-idempotency-key"] == ("a" * 72)
