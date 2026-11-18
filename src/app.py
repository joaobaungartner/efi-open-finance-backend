import os
import sys
from glob import glob
from importlib import import_module

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Segue o padrão de imports e de descoberta de rotas do template Insper Jr.
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config.settings import get_settings  # noqa: E402

settings = get_settings()
app = FastAPI(title="Efí Open Finance - Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.client_url],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "OK", "environment": settings.efi_environment}


for route_file in sorted(glob(os.path.join(SRC_DIR, "use_cases", "**", "index.py"), recursive=True)):
    relative_path = os.path.relpath(route_file, SRC_DIR)
    module_name = os.path.splitext(relative_path)[0].replace(os.path.sep, ".")
    module = import_module(module_name)
    if hasattr(module, "router"):
        app.include_router(module.router)
