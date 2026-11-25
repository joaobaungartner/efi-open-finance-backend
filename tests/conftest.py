import os

# Antes de importar src.app, porque Settings valida segredos na inicialização.
os.environ["APP_ENV"] = "test"
os.environ["DRY_RUN"] = "false"
os.environ["ADMIN_API_KEY"] = "admin-test-key-000000000000000000000000000000"
os.environ["EFI_WEBHOOK_HASH"] = "webhook-test-hash-00000000000000000000000000"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["EFI_ENVIRONMENT"] = "homologation"
