# Backend Efí Open Finance (FastAPI)

Backend mínimo baseado na estrutura de `murilozgodoy/template-backend-repo`: FastAPI, arquitetura `use_cases / repositories / entities / models`, descoberta automática de rotas, `pydantic-settings`, SQLAlchemy/Alembic, testes e Docker. O módulo de login JWT do template não foi copiado porque este serviço ainda não possui usuários; rotas internas usam uma chave administrativa. **Não é uma plataforma pronta para pagamentos em produção.**

## O que já faz

| Rota | Acesso | Finalidade |
| --- | --- | --- |
| `GET /health` | Público | Disponibilidade da API |
| `GET /openfinance/retorno` | Público | Efí devolve o usuário após a autorização (`identificadorPagamento` e possível `erro`) |
| `GET /webhooks/efi` | Público | Teste simples de disponibilidade |
| `POST /webhooks/efi?hmac=...` | Validado por segredo | Salva aviso de pagamento sem duplicar retransmissões |
| `GET /internal/efi/participantes` | `X-Admin-Key` | Consulta participantes Efí por mTLS + OAuth2 |
| `GET /internal/efi/config` | `X-Admin-Key` | Consulta configuração Efí e oculta segredo do webhook |

**Não há endpoint para enviar Pix.** Mesmo `status: aceito` em webhook não deve ser usado isoladamente como prova de crédito no destino; reconcilie pela API e pelo extrato da conta.

## 1. Executar localmente (Windows / Linux)

Requisitos: Python 3.12+ e, para PostgreSQL, Docker. Em desenvolvimento inicial, a configuração padrão utiliza SQLite para reduzir dependências; a arquitetura é a mesma e é possível trocar por PostgreSQL via `DATABASE_URL`.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
```

Copie `.env.example` para `.env` e configure **ambos** `ADMIN_API_KEY` e `EFI_WEBHOOK_HASH` com segredos diferentes gerados por:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Preencha depois os valores de **Homologação**: `EFI_CLIENT_ID`, `EFI_CLIENT_SECRET` e caminho completo do certificado `.p12` de Homologação. Não utilize credenciais de Produção com a URL de Homologação. Recomenda-se revogar as chaves anteriormente expostas em capturas de tela.

```bash
alembic upgrade head
uvicorn src.app:app --reload
```

- http://localhost:8000/health
- http://localhost:8000/docs
- http://localhost:8000/openfinance/retorno?identificadorPagamento=teste

## 2. Testar recebimento de webhooks

Envie com Postman `POST http://localhost:8000/webhooks/efi?hmac=VALOR_DO_EFI_WEBHOOK_HASH`, `Content-Type: application/json`, body:

```json
{
  "identificadorPagamento": "urn:efi:exemplo-123",
  "valor": "9.90",
  "status": "aceito",
  "tipo": "pagamento"
}
```

Uma repetição do mesmo JSON retorna `new_event: false`. Em dev os eventos ficam no `efi_dev.sqlite3`. A Efí, no modo `hmac`, envia o valor **como query string estática** (`?hmac=...`), não como uma assinatura criptográfica do body. Use HTTPS, segredo aleatório longo, `secrets.compare_digest`, mascaramento de query strings no proxy e restrinja o acesso aos logs. Para maior segurança em produção, considere mTLS no proxy.

## 3. Publicar sob HTTPS

Em produção, configure `APP_ENV=prod`, `CLIENT_URL=https://seu-frontend`, `DATABASE_URL=postgresql+psycopg://...` e `PUBLIC_BASE_URL=https://seu-backend`. Use um proxy HTTPS na frente do FastAPI e nunca exponha diretamente a porta de desenvolvimento. O `compose.yml` traz PostgreSQL para deploy controlado, com a API publicada apenas em `127.0.0.1:8000`.

A URL do webhook precisa **realmente responder via HTTPS** antes de configurar na Efí. Não use `localhost` no `PUBLIC_BASE_URL`.

## 4. Configurar a API Efí (apenas quando já tiver HTTPS)

O comando mostra o corpo sem segredos e **não envia nada por padrão**:

```bash
python -m scripts.configure_efi
```

Para gravar URLs em Homologação:

```bash
python -m scripts.configure_efi --apply
```

Produção exige sinalização explícita de ambos os flags e `EFI_ENVIRONMENT=production`:

```bash
python -m scripts.configure_efi --apply --allow-production
```

O comando usa `PUT /v1/config` com `redirectURL`, `webhookURL`, `webhookSecurity:{"type":"hmac","hash":"..."}` e `processPayment:async`. Evite executar em Produção até testar em Homologação. A configuração é por **aplicação/credenciais**, não apenas pela URL.

## 5. Consultas administrativas (Postman)

`GET /internal/efi/participantes` e `GET /internal/efi/config` exigem `X-Admin-Key: <ADMIN_API_KEY>`. Os métodos fazem OAuth2 `client_credentials` e mTLS carregando o arquivo `.p12` com `cryptography`. O arquivo fica local no servidor, **fora do repositório**.

## 6. Testes

```bash
pytest
ruff check .
ruff format --check .
```

## Cuidados antes do MVP financeiro

- Nunca aceitar o status do navegador como prova de pagamento.
- Nunca movimentar dinheiro automaticamente com base no valor de uma venda ainda não liquidada.
- Não exibir tokens/chaves/certificados em logs, prints, commits ou imagens.
- Remover `?hmac=` dos logs do proxy/CDN/monitoramento.
- Persistir idempotência e auditar cada pedido antes de acrescentar `POST /v1/pagamentos/pix`.
- Em produção, adotar autenticação de operador com permissões e MFA, controle de aprovação e reconciliação independente.

## Referências

- Template: https://github.com/murilozgodoy/template-backend-repo
- Configuração Efí: https://dev.efipay.com.br/docs/api-open-finance/configuracoes-de-aplicacao/
- Webhooks Efí: https://dev.efipay.com.br/docs/api-open-finance/webhooks/
- Credenciais: https://dev.efipay.com.br/docs/api-open-finance/credenciais/
