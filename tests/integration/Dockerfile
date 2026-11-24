FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY tests/conftest.py ./tests/conftest.py
COPY tests/integration ./tests/integration
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests/integration"]
