"""Start a loopback-only sandbox with a fresh database and temporary secrets."""

import os
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import URL


def main():
    with TemporaryDirectory(prefix="efi-open-finance-") as directory:
        database_url = URL.create(
            "sqlite", database=(Path(directory) / "local.sqlite3").as_posix()
        )
        serve_local(str(database_url))


def serve_local(database_url: str):
    root = Path(__file__).resolve().parents[1]
    admin_key = token_urlsafe(32)
    os.environ.update(
        EFI_LOCAL_SANDBOX="1",
        APP_ENV="dev",
        DRY_RUN="true",
        EFI_ENVIRONMENT="homologation",
        DATABASE_URL=database_url,
        CLIENT_URL="http://127.0.0.1:5173",
        ADMIN_API_KEY=admin_key,
        EFI_WEBHOOK_HASH=token_urlsafe(32),
    )
    for name in (
        "EFI_CLIENT_ID", "EFI_CLIENT_SECRET", "EFI_CERTIFICATE_PATH",
        "EFI_CERTIFICATE_PASSWORD", "PUBLIC_BASE_URL",
    ):
        os.environ.pop(name, None)

    # Import the app after setting DATABASE_URL, before Alembic reuses its engine.
    from src.app import app

    from database.database import engine

    try:
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "head")
        print(f"Chave administrativa temporária para o painel local: {admin_key}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
