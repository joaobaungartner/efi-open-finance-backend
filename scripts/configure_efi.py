"""Cadastrar na Efí URLs HTTPS reais. Requer execução explícita com --apply.

Exemplo:
    python -m scripts.configure_efi           # dry run, não envia nada
    python -m scripts.configure_efi --apply   # PUT /v1/config homologação
"""
import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import httpx  # noqa: E402

from config.settings import get_settings  # noqa: E402
from services.efi_api_client import EfiOpenFinanceClient  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Executar PUT real de configuração")
    parser.add_argument(
        "--allow-production", action="store_true", help="Necessário para configurar Produção"
    )
    args = parser.parse_args()
    settings = get_settings()
    base = (settings.public_base_url or "").rstrip("/")
    if not base.startswith("https://"):
        parser.error("Defina PUBLIC_BASE_URL com domínio HTTPS público")
    if settings.efi_environment == "production" and not args.allow_production:
        parser.error("Produção requer --allow-production de forma explícita")
    redirect = f"{base}/openfinance/retorno"
    webhook = f"{base}/webhooks/efi"
    print(f"Ambiente: {settings.efi_environment}")
    print(f"redirectURL: {redirect}")
    print(f"webhookURL: {webhook}")
    print("webhookSecurity.type: hmac (segredo omitido)")
    print("processPayment: async")
    if not args.apply:
        print("Dry run: nada foi enviado. Acrescente --apply para configurar.")
        return
    try:
        result = EfiOpenFinanceClient(settings).configure_urls(
            redirect, webhook, settings.efi_webhook_hash.get_secret_value()
        )
    except httpx.HTTPError as exc:
        print(f"Efí recusou a configuração: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from exc
    if isinstance(result.get("webhookSecurity"), dict):
        result["webhookSecurity"].pop("hash", None)
    print("Configuração enviada com sucesso.")
    print(result)


if __name__ == "__main__":
    main()
