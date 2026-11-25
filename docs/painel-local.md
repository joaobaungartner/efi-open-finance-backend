# Painel local do Efí Open Finance

## Executar no VS Code

Abra a pasta que contém os dois repositórios. Use Python 3.12 ou 3.13 e Node 22.12+ da série LTS do Node 22. No primeiro terminal PowerShell, a partir dessa pasta, execute:

```powershell
cd efi-open-finance-backend
python -m venv .venv-local
.\.venv-local\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv-local\Scripts\python.exe -m scripts.run_local
```

No segundo terminal, a partir da pasta que contém os dois repositórios:

```powershell
cd efi-open-finance-frontend
npm.cmd ci
npm.cmd run dev
```

Abra [o painel](http://127.0.0.1:5173) e digite a chave administrativa temporária exibida no primeiro terminal. Ela fica na memória da página. Um novo backend gera uma nova chave. No painel, escolha **Simular Pix**, selecione Stone ou PagBank e informe um alias como `teste@example.invalid` e um valor de teste como `12,34`. A prévia mascara o favorecido e omite o CPF sintético. Dashboard e listas começam vazios. A prévia existe somente na resposta e na página aberta.

O launcher cria um arquivo SQLite exclusivo em uma pasta temporária, aplica as migrações do Alembic nesse banco e serve a API em `127.0.0.1:8001`. As requisições concorrentes usam conexões independentes para o mesmo arquivo. Ao encerrar com Ctrl+C, o launcher fecha as conexões e remove a pasta que criou. Um encerramento forçado pode deixar a pasta temporária no disco. A próxima execução sempre cria um banco vazio. O launcher desativa a leitura do `.env` e remove as variáveis de credenciais bancárias herdadas do processo. O Vite encaminha as chamadas ao loopback. A opção local dispensa Docker. O Compose existente usa PostgreSQL 16 com volume persistente, variáveis de configuração e um certificado montado. O launcher local tem um caminho de execução independente.

## Gestão de Maquininhas

No menu **Gestão de Maquininhas**, cadastre dois clientes fictícios. Em **Contas de adquirência**, escolha Stone e informe uma referência fictícia. Abra **Maquininhas**, selecione a conta e cadastre um identificador fictício de terminal.

Em **Vínculos**, consulte a data efetiva do primeiro vínculo e selecione o Cliente A. Para transferir, consulte uma data efetiva posterior, selecione o Cliente B, informe o motivo e confirme. O histórico mostra o fim do vínculo A e o início do vínculo B no mesmo instante. Use **Consultar data efetiva** para identificar o cliente antes e depois da troca. A tela interpreta a data e hora no fuso do navegador. A API exige um deslocamento explícito ou `Z` e normaliza para UTC.

A revisão `0006_gestao_terminais` cria clientes, adquirentes, contas, terminais, vínculos e auditoria. O catálogo inicial contém Stone, PagBank, Rede e C6. Cada conta pertence a uma adquirente. A referência da conta é única por adquirente e ambiente. O identificador do terminal é único por conta. Um terminal pode ter vários vínculos históricos. A consulta temporal usa `início <= instante < fim` e retorna vínculo nulo quando não existe uma associação inequívoca. Desativar um cadastro impede novos vínculos com ele, preservando as vigências registradas. Vínculos cujo fim já passou ficam disponíveis somente para consulta.

No PostgreSQL, a restrição de exclusão GiST usa faixas nativas e impede sobreposições inclusive em escritas diretas. A API também bloqueia a linha do terminal durante a transferência. No SQLite, gatilhos de inserção e atualização impedem sobreposição, e a API inicia a transação de escrita com `BEGIN IMMEDIATE`. Isso serializa escritores no arquivo todo. Sob contenção prolongada, a API pode devolver 503. A transferência verifica o ID e o fim do vínculo consultado para detectar alterações concorrentes. A auditoria é gravada junto com a operação e registra a origem como chave administrativa compartilhada.

Uma transferência pode ter data retroativa com justificativa obrigatória, desde que seja respeitada a vigência e que o vínculo consultado ainda não tenha encerrado. A operação é auditada e preserva os intervalos anteriores. Quando houver vendas e repasses, uma transferência retroativa poderá exigir revisão específica das atribuições de vendas e das apurações do período afetado.

As rotas abaixo usam o prefixo `/internal/gestao/v1` e exigem `X-Admin-Key`:

- `GET /resumo` consulta os indicadores.
- `GET/POST /clientes` e `GET/PATCH /clientes/{id}` consultam, cadastram, editam e alteram o estado do cliente.
- `GET /instituicoes` consulta o catálogo de adquirentes.
- `GET/POST /contas-adquirencia` consulta e cadastra contas.
- `GET/POST /terminais` e `GET/PATCH /terminais/{id}` consultam e mantêm terminais. As consultas aceitam o instante opcional `em`.
- `GET/POST /terminais/{id}/vinculos` consulta o histórico e cria um intervalo.
- `POST /terminais/{id}/transferencias` fecha o vínculo anterior e cria outro na mesma transação. O corpo inclui `cliente_id`, `vinculo_anterior_id`, `fim_anterior` (nulo para vínculo aberto), `efetiva_em` e `motivo`.

## Diagnóstico do checkout

- O FastAPI descobre routers em `src/use_cases/**/index.py`. Configuração e chave administrativa ficam em Pydantic Settings. SQLAlchemy gerencia sessões e Alembic controla o esquema. A revisão `0005_pix_investigations`, posterior à revisão descrita no `CONTEXTO.md`, registra a auditoria de investigações.
- O cliente Efí usa mTLS e OAuth. Antes de um POST de iniciação, o serviço Python reserva a solicitação e confirma a mudança para `submitting`, com idempotência local e bancária. O resultado pode exigir autorização ou ficar indeterminado. O webhook tem deduplicação própria e o retorno do navegador é informativo.
- A reconciliação consulta por período, compara o ID próprio e verifica consistência. O checkout expõe POST administrativo de investigação e mantém o GET legado. Ambos consultam a Efí e registram o resultado, sem atualizar o estado financeiro. O painel exibe o histórico registrado. A investigação remota é desativada no modo local.
- O painel acrescenta leitura de diagnósticos persistidos com lista de códigos internos conhecidos e status HTTP. Mantém as consultas administrativas, paginação, filtros e controle por chave. A iniciação existente continua restrita ao serviço Python em homologação. Com `DRY_RUN` o serviço bloqueia a iniciação antes da reserva e o cliente bloqueia chamadas externas antes de carregar certificado ou obter token.
- O catálogo Stone/PagBank de simulação contém identificadores fictícios. Este fluxo verifica formato e comportamento local. A disponibilidade de cada instituição, elegibilidade de contas PJ, consentimento, callbacks e liquidação exigem validação autorizada em um ambiente bancário apropriado.
- O módulo Efí cobre o fluxo administrativo inicial de Pix. A gestão cobre cadastros e vínculos de terminais. Identidades individuais, vendas, recebíveis, ledger e apuração de repasses ficam para etapas posteriores.

## Rotas usadas pela interface

- `GET /health` identifica simulação, homologação ou produção.
- `GET /internal/efi/pix/pagamentos` lista solicitações, diagnósticos e totais por estado.
- `GET /internal/efi/pix/pagamentos/pendentes` consulta pendências por estado e idade.
- `GET /internal/efi/pix/pagamentos/{payment_id}/investigacoes` lista o histórico auditado.
- `GET /internal/efi/participantes` lista participantes da Efí ou o catálogo explicitamente marcado como simulação.
- `POST /internal/efi/pix/simulacoes` valida a solicitação de simulação e retorna a prévia sanitizada.

## Verificação

No backend, execute `.\.venv-local\Scripts\python.exe -m pytest -ra`. Com Docker ativo, `& ./tests/integration/run-postgres.ps1` executa a suíte em PostgreSQL descartável com rede interna. No frontend, execute `npm.cmd run build`.
