# bambu-bridge — integração NerdGeek

Microserviço **REST** (`bambu-bridge`) para monitorar impressoras **Bambu Lab** pela **nuvem** (MQTT + API HTTP), com cache em **SQLite** (preparado para **PostgreSQL** via `DATABASE_URL`), snapshots de câmera quando disponíveis, e contrato estável para o **NerdGeek**.

A biblioteca existente (`bambulab`) **não foi removida nem substituída**; o bridge é uma camada nova em `app/bridge/`.

---

## Visão geral

| Responsabilidade | Implementação |
|------------------|---------------|
| Autenticação | `BAMBU_TOKEN` preferencial; senão arquivo `~/.bambu_token` ou `BAMBU_TOKEN_FILE`; senão `BAMBU_USERNAME` + `BAMBU_PASSWORD` (sem prompt 2FA no servidor). |
| Listagem de impressoras | API `get_devices` + tabela `printers`. |
| Tempo real | Um cliente MQTT por impressora (`MQTTClient`), cache em `printer_status_cache`. |
| Resiliência | Se o MQTT ficar **stale** (sem atualização > `MQTT_STALE_SECONDS`), o job periódico usa `get_print_status(force=True)`. |
| Câmera | Tenta URL/snapshot via API (`get_camera_urls` / endpoint de snapshot); se não houver, usa **JPEG na LAN** (A1/P1) com `BAMBU_PRINTER_HOST_MAP` + `dev_access_code` da cloud. |
| Histórico | Amostras em `printer_status_history` (no máx. `HISTORY_MAX_PER_PRINTER`), intervalo mínimo `HISTORY_SNAPSHOT_INTERVAL_SECONDS` no fluxo MQTT. |

**Não** usa `servers/proxy.py` como API pública.

---

## Configuração de credenciais

Copie `.env.example` para `.env` e preencha:

1. **`BAMBU_TOKEN`** (recomendado em produção) — token Bearer da Bambu.
2. Ou monte/salve o arquivo JSON do CLI em **`BAMBU_TOKEN_FILE`** ou `~/.bambu_token`.
3. Ou **`BAMBU_USERNAME`** + **`BAMBU_PASSWORD`** — **não funciona com 2FA** no container sem interação; veja abaixo.
4. **`BAMBU_UID`** — opcional; se vazio, obtido de `GET /v1/user-service/my/profile`.
5. **`API_TOKEN`** — se definido, todas as rotas abaixo de `/api/` exigem header `Authorization: Bearer <API_TOKEN>`. **`/health` não exige token.**

Variáveis úteis: `DATABASE_URL`, `SNAPSHOT_DIR`, `SNAPSHOT_INTERVAL_SECONDS`, `POLL_INTERVAL_SECONDS`, `BAMBU_PRINTER_HOST_MAP` (JSON), `LOG_LEVEL`.

---

## Primeira autenticação (2FA / e-mail)

O servidor **não** pede código interativo. Para contas com verificação:

1. No seu PC (com Python e o repo), rode o CLI de login do projeto, por exemplo:
   ```bash
   python cli_tools/login.py --username seu@email.com
   ```
   (ou o caminho equivalente após `pip install -e .`).
2. Isso grava `~/.bambu_token` (JSON com `token` e `region`).
3. **Docker:** monte esse arquivo como somente leitura e use o mesmo caminho dentro do container, por exemplo:
   - Volume: `C:\Users\SEU_USUARIO\.bambu_token:/root/.bambu_token:ro`
   - E defina `BAMBU_TOKEN_FILE=/root/.bambu_token` **ou** confie no padrão `~/.bambu_token` do usuário do processo (no container, usuário root → `/root/.bambu_token`).
4. Alternativa: copie o valor de `token` do JSON para **`BAMBU_TOKEN`** no `.env` (rotação manual quando expirar).

Renovação futura de token: hoje o fluxo é recarregar env/arquivo ou relogar via CLI; o código concentra a resolução do token em `BridgeRuntime.resolve_token_and_client()` para evoluir com refresh automático depois.

---

## Rodar localmente

```bash
# Na raiz do repositório
pip install -r requirements.txt -r requirements-bridge.txt

# Windows PowerShell
$env:PYTHONPATH = "$PWD\app"

# Linux/macOS
export PYTHONPATH="$(pwd)/app"

uvicorn bridge.main:app --host 0.0.0.0 --port 8010
```

Garanta que o diretório `storage/` exista ou use `DATABASE_URL`/`SNAPSHOT_DIR` apontando para caminhos válidos.

---

## Docker Compose

```bash
cp .env.example .env
# Edite .env (BAMBU_TOKEN ou credenciais, API_TOKEN, etc.)
mkdir -p storage
docker compose up -d --build
```

- Serviço: **`bambu-bridge`**, porta **8010**.
- Volume **`./storage`** → banco SQLite e snapshots.
- Healthcheck HTTP: **`GET /health`**.
- Para token via CLI, descomente e ajuste o volume de `.bambu_token` em `docker-compose.yml`.

Imagem: `Dockerfile` na raiz; `PYTHONPATH=/app/app_pkg` com pacote `bridge` em `app/bridge`.

---

## Contrato da API (NerdGeek)

Base: `http://<host>:8010`

### Autenticação

Se `API_TOKEN` estiver definido:

```http
Authorization: Bearer <API_TOKEN>
```

### Endpoints

| Método | Caminho | Descrição |
|--------|---------|-----------|
| GET | `/health` | Saúde do serviço (sem Bearer). |
| GET | `/api/v1/printers` | Lista impressoras. |
| GET | `/api/v1/printers/{printer_id}` | Detalhe + AMS embutido. |
| GET | `/api/v1/printers/{printer_id}?advanced_refresh=true` | Detalhe + AMS embutido com tentativa opcional de refresh avançado read-only. |
| GET | `/api/v1/printers/{printer_id}/status` | Painel simplificado. |
| GET | `/api/v1/printers/{printer_id}/status?advanced_refresh=true` | Painel com tentativa opcional de refresh avançado read-only. |
| GET | `/api/v1/printers/{printer_id}/ams` | AMS normalizado. |
| GET | `/api/v1/printers/{printer_id}/camera/snapshot` | Último JPEG; `?refresh=true` força nova captura. |
| GET | `/api/v1/printers/{printer_id}/history` | Histórico recente (`limit`, máx. 500). |
| GET | `/api/v1/printers/{printer_id}/debug/raw` | **Temporário**: último payload bruto + AMS bruto + highlights + timestamps (mascarado). |
| GET | `/api/v1/printers/{printer_id}/debug/normalized` | **Temporário**: campos extraídos, inferidos e origem de cada campo. |
| POST | `/api/v1/sync/devices` | Sincroniza lista da conta e reinicia MQTT. |
| POST | `/api/v1/printers/{printer_id}/refresh` | Atualiza cache pela cloud (+ merge MQTT se existir), grava histórico, tenta `pushall` MQTT. |
| POST | `/api/v1/printers/{printer_id}/refresh-advanced` | Força refresh avançado read-only e retorna método/tempo/campos obtidos. |

`printer_id` é o **id interno** (inteiro) da tabela `printers`, não o serial.

### Exemplo: `GET /health`

```json
{
  "api": "ok",
  "db": "ok",
  "bambu_auth": "ok",
  "mqtt_connections": 2,
  "printers_count": 2
}
```

### Exemplo: `GET /api/v1/printers/{id}/status`

```json
{
  "printer_id": 1,
  "name": "Bambu A1 - Linha 01",
  "online": true,
  "state": "RUNNING",
  "print_status": "RUNNING",
  "progress_percent": 42,
  "job_name": "Suporte_Celular_v3.3mf",
  "eta_minutes": 58,
  "current_layer": 112,
  "total_layers": 265,
  "nozzle_temp": 219.4,
  "bed_temp": 64.8,
  "last_seen": "2026-04-07T20:00:00Z"
}
```

Campos de erro enriquecidos (quando houver HMS/erro):
- `error_code`
- `error_message` (mantido por compatibilidade)
- `error_attr`
- `error_action`
- `error_timestamp`
- `error_raw` (objeto bruto parseado)

### Exemplo: `GET /api/v1/printers/{id}/ams`

```json
{
  "printer_id": 1,
  "has_ams": true,
  "active_slot": 2,
  "slots": [
    {"slot": 1, "material": "PLA", "color": "#FFFFFF", "name": null},
    {"slot": 2, "material": "PLA", "color": "#000000", "name": null},
    {"slot": 3, "material": "PETG", "color": "#FF0000", "name": null},
    {"slot": 4, "material": null, "color": null, "name": null}
  ],
  "updated_at": "2026-04-07T20:00:00Z"
}
```

*(Cores/nomes reais dependem do payload MQTT/API.)*

---

## Testar com curl

```bash
# Health (sem token)
curl -s http://127.0.0.1:8010/health | jq .

# Lista (com token)
curl -s -H "Authorization: Bearer $API_TOKEN" http://127.0.0.1:8010/api/v1/printers | jq .

# Snapshot com refresh
curl -s -o snap.jpg -H "Authorization: Bearer $API_TOKEN" \
  "http://127.0.0.1:8010/api/v1/printers/1/camera/snapshot?refresh=true"
```

Script automatizado: `python scripts/smoke_test.py --base http://127.0.0.1:8010 --token <API_TOKEN>`.

Plano de testes: `TEST_PLAN.md`.

### Depuração fina A1 (temporária)

- Ative `DEBUG_RAW_PAYLOADS=true` para gravar amostras em `./storage/debug/printer_<id>.json`.
- Esses arquivos não incluem token/senha (campos sensíveis são mascarados).
- Use:
  - `GET /api/v1/printers/{id}/debug/raw`
  - `GET /api/v1/printers/{id}/debug/normalized`
  para ajustar parser sem quebrar o contrato principal.

### Advanced Refresh (somente leitura)

Use para tentar preencher campos que chegam nulos em payload curto da A1.

Configuração (`.env`):
- `ADVANCED_REFRESH_ENABLED=true`
- `ADVANCED_REFRESH_TIMEOUT_SECONDS=5`
- `ADVANCED_REFRESH_ON_NULL_FIELDS=true`
- `ONLINE_STALE_SECONDS=120`

Comportamento:
- Seguro/read-only: usa apenas
  - MQTT `pushall` (request de status completo),
  - cloud `get_print_status(force=True)`,
  - cloud `get_ams_filaments`.
- Não envia comandos de controle (sem print/movimento/temperatura/pause/stop).
- Se não houver ganho, mantém os dados já disponíveis.
- Regra de `online`:
  - `online=true` quando houver atualização recente (janela `ONLINE_STALE_SECONDS`) e sinais de atividade (`state`, `print_status`, `progress_percent`, `job_name`, `current_layer`, `total_layers`, `nozzle_temp`, `bed_temp`, `network_signal`);
  - evita degradar para `online=false` com payload MQTT curto.

### Cache inteligente de AMS (context-aware)

A API Bambu pode devolver AMS parcial/intermitente. O bridge aplica cache robusto para evitar regressão visual:

- **Quality score** por payload AMS:
  - `+1` por slot com índice
  - `+1` por `material`
  - `+1` por `color`
  - `+1` por `name`
  - `+1` por `type`
  - `+2` se `active_slot` presente
  - `+1` se `has_ams=true`
- **Preservação inteligente**: se chegar payload fraco e o cache anterior for melhor, preserva o cache.
- **Capability histórica por impressora**:
  - `ams_capability_confirmed`
  - `ams_last_confirmed_at`
  - `ams_last_confirmed_source`
  - `ams_last_good_payload`
  Isso separa capacidade física histórica do payload instantâneo.
- **Contexto de impressão**: usa `job_name`, `task_id`, `print_status`, `progress` para decidir se pode reaproveitar o AMS anterior.
- **Invalidar em mudança de contexto**:
  - `task_id` mudou
  - `job_name` mudou
  - queda brusca de progresso (ex.: 80 -> 5)
- **TTL**: após `AMS_CACHE_PRESERVE_SECONDS`, status vira `stale`.
- **Sem regressão brusca em impressora com AMS confirmado**:
  - payload fraco não derruba imediatamente `has_ams` para `false` enquanto dentro de TTL e sem evidência de troca.

Novos campos nos endpoints `/api/v1/printers/{id}` e `/api/v1/printers/{id}/ams`:
- `cache_preserved`
- `quality_score`
- `ams_status` (`fresh | preserved | stale | pending_refresh`)
- `last_good_update_at`
- `context_job_name`
- `context_task_id`
- `data_source` (`mqtt`, `cloud`, `advanced_refresh`, etc.)
- `ams_detected_struct`
- `external_spool_configured`
- `filament_source` (`ams | external | unknown`)
- `filament` (`source`, `material`, `color`, `name`, `type`)
- `ams_capability_confirmed`
- `ams_capability_confidence`
- `ams_last_confirmed_at`
- `ams_last_confirmed_source`

Campos por slot AMS (enriquecidos):
- `slot`
- `material`
- `color` (compatível: valor bruto normalizado sem `#`)
- `color_raw` (valor original do payload)
- `color_hex` (normalizado para UI, ex. `#C52C18`)
- `name`
- `type`
- `source_index` (índice/origem no payload bruto, ex. `id` da bandeja)

Observação para **A1 sem AMS físico**:
- é comum o payload não trazer dados suficientes de carretel externo;
- nesse caso, o bridge responde `filament_source=unknown` e `filament` com valores `null`;
- o bridge não força `has_ams=true` sem evidência real (slots com dados, `active_slot`, `ams_root` ou indicador explícito).

Exemplos:

```bash
# Forçar refresh avançado explícito
curl -s -X POST -H "Authorization: Bearer $API_TOKEN" \
  http://127.0.0.1:8010/api/v1/printers/1/refresh-advanced | jq .

# Status com refresh avançado antes da resposta
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://127.0.0.1:8010/api/v1/printers/1/status?advanced_refresh=true" | jq .

# Detalhe com refresh avançado
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://127.0.0.1:8010/api/v1/printers/1?advanced_refresh=true" | jq .
```

---

## Integração NerdGeek

1. Defina um **`API_TOKEN`** compartilhado.
2. Faça polling ou Webhooks do seu lado a partir de `/api/v1/printers` e `/api/v1/printers/{id}/status`.
3. Use **`last_seen` / `last_update`** para detectar impressora offline ou MQTT parado.
4. Trate **`503`**/`404` em snapshot como “câmera indisponível” (LAN não configurada ou sem URL cloud).

OpenAPI: `http://127.0.0.1:8010/docs`.

---

## Câmera A1 / P1 (JPEG)

- **LAN:** stream TLS porta **6000**, um frame JPEG por leitura (`JPEGFrameStream` em `bambulab.video`).
- **Nuvem:** não há SDK TUTK neste serviço; a API pode expor URL de snapshot em alguns cenários — o bridge tenta `get_camera_urls` e endpoint dedicado. Se falhar, configure **`BAMBU_PRINTER_HOST_MAP`** com o IP local da impressora (ex.: `{"01P00A123456789":"192.168.1.50"}`). O **access code** vem da API de bind (`dev_access_code`), armazenado no banco — **nunca é logado**.

---

## PostgreSQL (futuro)

Defina por exemplo:

```env
DATABASE_URL=postgresql+psycopg2://user:pass@db:5432/bambu_bridge
```

Instale o driver (`psycopg2-binary`). O mesmo código SQLAlchemy funciona; para migrações versionadas você pode adicionar **Alembic** depois (hoje usa `create_all` na subida).

---

## Limitações conhecidas

- **2FA** no processo do container sem volume/CLI: não suportado.
- **TUTK / P2P** da Bambu para vídeo remoto não está integrado (dependência proprietária).
- **Controle de impressão / G-code** não faz parte desta fase (somente leitura).
- Broker MQTT fixo em `bambulab.mqtt` (`us.mqtt.bambulab.com`); contas em outra região podem exigir evolução da biblioteca.

---

## Arquitetura de pastas

```
app/bridge/
  main.py           # FastAPI + lifespan + scheduler
  config.py
  api/              # Rotas
  services/         # Runtime Bambu, sync, MQTT, polling, câmera, persistência
  models/           # SQLAlchemy
  schemas/          # Pydantic
  storage/          # Marcador (dados em disco via env)
  utils/
```

Documentação de testes: **`TEST_PLAN.md`**. Smoke: **`scripts/smoke_test.py`**.
