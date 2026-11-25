import re
from datetime import date
import ssl
import tempfile
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from config.settings import Settings, get_settings


class EfiConfigurationError(RuntimeError):
    pass


class EfiUnexpectedResponse(httpx.HTTPError):
    def __init__(self, status_code: int):
        super().__init__("Resposta Efí inesperada")
        self.status_code = status_code


class EfiOAuthError(httpx.HTTPError):
    """HTTP or response failure while obtaining a token before sending Pix."""

    def __init__(self, error: httpx.HTTPError):
        super().__init__("Falha na etapa OAuth Efí")
        self.error = error


class EfiOpenFinanceClient:
    """Cliente Efí com mTLS e iniciação Pix restrita à homologação."""

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
        token = self._json_object(response).get("access_token")
        if not isinstance(token, str) or not token:
            raise EfiUnexpectedResponse(response.status_code)
        return token

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise EfiUnexpectedResponse(response.status_code) from exc
        if not isinstance(data, dict):
            raise EfiUnexpectedResponse(response.status_code)
        return data

    def _request(
        self,
        method: str,
        endpoint: str,
        body: dict | None = None,
        *,
        idempotency_key: str | None = None,
        params: dict | None = None,
    ) -> dict:
        if self.settings.dry_run:
            raise EfiConfigurationError("Acesso externo à Efí bloqueado em DRY_RUN")
        try:
            ssl_context = self._ssl_context()
        except OSError as exc:
            raise EfiConfigurationError("Falha ao carregar certificado Efí") from exc

        with httpx.Client(
            base_url=self.settings.efi_api_base,
            verify=ssl_context,
            timeout=20,
        ) as client:
            try:
                token = self._token(client)
            except httpx.HTTPError as exc:
                if idempotency_key is not None:
                    raise EfiOAuthError(exc) from exc
                raise
            response = client.request(
                method,
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    **(
                        {"x-idempotency-key": idempotency_key}
                        if idempotency_key is not None
                        else {}
                    ),
                },
                json=body,
                params=params,
            )
            return self._json_object(response)

    def start_pix(self, payload: dict, *, idempotency_key: str) -> dict:
        """Solicita Pix com idempotência exclusivamente em Homologação."""
        if self.settings.dry_run:
            raise EfiConfigurationError("Iniciação Pix bloqueada em DRY_RUN")
        if self.settings.efi_environment != "homologation":
            raise EfiConfigurationError(
                "Iniciação Pix bloqueada fora de Homologação"
            )

        if (
            not isinstance(idempotency_key, str)
            or re.fullmatch(
                r"[A-Za-z0-9]{72}", idempotency_key
            ) is None
        ):
            raise ValueError("Chave de idempotência inválida")

        required = ("pagador", "favorecido", "pagamento")
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(field), dict) for field in required
        ):
            raise ValueError("Payload Pix inválido")

        return self._request(
            "POST",
            "/v1/pagamentos/pix",
            payload,
            idempotency_key=idempotency_key,
        )

    def list_pix_payments(
        self,
        inicio: str,
        fim: str,
        *,
        pagina: int = 1,
        quantidade: int = 50,
    ) -> dict:
        """Consulta pagamentos Pix; não modifica estados locais."""
        if self.settings.efi_environment != "homologation":
            raise EfiConfigurationError(
                "Consulta Pix limitada à Homologação nesta etapa"
            )

        def parse_date(value: str) -> date:
            if (
                not isinstance(value, str)
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None
            ):
                raise ValueError("Data inválida: use AAAA-MM-DD")
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Data inválida") from exc

        start = parse_date(inicio)
        end = parse_date(fim)

        if end < start:
            raise ValueError("Data final anterior à inicial")

        if type(pagina) is not int or pagina < 1:
            raise ValueError("Página inválida")

        if (
            type(quantidade) is not int
            or not 1 <= quantidade <= 100
        ):
            raise ValueError("Quantidade inválida")

        return self._request(
            "GET",
            "/v1/pagamentos/pix",
            params={
                "inicio": start.isoformat(),
                "fim": end.isoformat(),
                "pagina": pagina,
                "quantidade": quantidade,
            },
        )

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
