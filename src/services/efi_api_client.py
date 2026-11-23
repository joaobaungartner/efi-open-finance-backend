import ssl
import tempfile
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from config.settings import Settings, get_settings


class EfiConfigurationError(RuntimeError):
    pass


class EfiOpenFinanceClient:
    """Cliente somente para autenticação, leitura e configuração da API Efí.

    Deliberadamente não oferece POST /pagamentos/pix nesta versão.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _ssl_context(self) -> ssl.SSLContext:
        path = self.settings.efi_certificate_path
        if not path or not Path(path).is_file():
            raise EfiConfigurationError("Configure EFI_CERTIFICATE_PATH com .p12 válido")
        password = (
            self.settings.efi_certificate_password.get_secret_value().encode("utf-8")
            if self.settings.efi_certificate_password
            else None
        )
        try:
            key, cert, additional = pkcs12.load_key_and_certificates(
                Path(path).read_bytes(), password
            )
        except (ValueError, TypeError) as exc:
            raise EfiConfigurationError("Certificado .p12 ou senha inválidos") from exc
        if key is None or cert is None:
            raise EfiConfigurationError("Arquivo .p12 não contém chave e certificado")

        context = ssl.create_default_context()
        with tempfile.TemporaryDirectory() as temporary:
            certificate_path = Path(temporary) / "cert.pem"
            private_key_path = Path(temporary) / "private-key.pem"
            cert_pem = cert.public_bytes(serialization.Encoding.PEM)
            for extra in additional or []:
                cert_pem += extra.public_bytes(serialization.Encoding.PEM)
            certificate_path.write_bytes(cert_pem)
            private_key_path.write_bytes(
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            certificate_path.chmod(0o600)
            private_key_path.chmod(0o600)
            context.load_cert_chain(str(certificate_path), str(private_key_path))
        return context

    def _credentials(self) -> tuple[str, str]:
        if not self.settings.efi_client_id or not self.settings.efi_client_secret:
            raise EfiConfigurationError("Configure EFI_CLIENT_ID e EFI_CLIENT_SECRET")
        return (
            self.settings.efi_client_id.get_secret_value(),
            self.settings.efi_client_secret.get_secret_value(),
        )

    def _token(self, client: httpx.Client) -> str:
        user, secret = self._credentials()
        response = client.post(
            "/v1/oauth/token",
            auth=httpx.BasicAuth(user, secret),
            json={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise EfiConfigurationError("Efí não retornou access_token")
        return token

    def _request(self, method: str, endpoint: str, body: dict | None = None) -> dict:
        with httpx.Client(
            base_url=self.settings.efi_api_base,
            verify=self._ssl_context(),
            timeout=20,
        ) as client:
            token = self._token(client)
            response = client.request(
                method,
                endpoint,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json=body,
            )
            response.raise_for_status()
            return response.json()

    def start_pix(self, payload: dict) -> dict:
        """Solicita iniciação Pix exclusivamente em Homologação."""
        if self.settings.efi_environment != "homologation":
            raise EfiConfigurationError(
                "Iniciação Pix bloqueada fora de Homologação"
            )

        required = ("pagador", "favorecido", "pagamento")
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(field), dict) for field in required
        ):
            raise ValueError("Payload Pix inválido")

        return self._request("POST", "/v1/pagamentos/pix", payload)

    def get_participants(self) -> dict:
        return self._request("GET", "/v1/participantes")

    def get_config(self) -> dict:
        return self._request("GET", "/v1/config")

    def configure_urls(self, redirect_url: str, webhook_url: str, hash_value: str) -> dict:
        if not (redirect_url.startswith("https://") and webhook_url.startswith("https://")):
            raise ValueError("As URLs da Efí precisam ser HTTPS")
        return self._request(
            "PUT",
            "/v1/config",
            {
                "redirectURL": redirect_url,
                "webhookURL": webhook_url,
                "webhookSecurity": {"type": "hmac", "hash": hash_value},
                "processPayment": "async",
            },
        )
