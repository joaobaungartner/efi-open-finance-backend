# MVP — Backend Efí Open Finance

## Objetivo
Receber retornos e webhooks da Efí, consultar participantes/configuração Open Finance e registrar eventos para posterior conciliação.

## Escopo
- Endpoint GET de retorno do usuário após autorização.
- Webhook POST protegido por segredo `hmac` configurado na Efí.
- Persistência idempotente, em PostgreSQL em produção.
- Cliente mTLS/OAuth2 para consulta e cadastro explícito de URLs Efí.
- Endpoints administrativos somente de leitura protegidos por `X-Admin-Key`.

## Fora do escopo deste MVP
- Iniciar Pix real a partir da Stone ou PagBank.
- Confirmar efetiva liquidação apenas com callback/webhook.
- Sistema de login multiusuário, dashboard e conciliação completa.
- Integrações financeiras diretas de consulta a Stone e PagBank.

## Evolução
1. Confirmar em Homologação a entrega segura do webhook.
2. Implantar em domínio HTTPS, cadastro de URLs via CLI.
3. Adicionar use case de iniciacão após validações técnicas e controle de acesso forte.
4. Incluir antifraude, idempotência transacional, autenticação de usuários e trilhas de auditoria.
