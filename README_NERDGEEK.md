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
| GET | `/api/v1/printers/{printer_id}/status` | Painel simplificado. |
| GET | `/api/v1/printers/{printer_id}/ams` | AMS normalizado. |
| GET | `/api/v1/printers/{printer_id}/camera/snapshot` | Último JPEG; `?refresh=true` força nova captura. |
| GET | `/api/v1/printers/{printer_id}/history` | Histórico recente (`limit`, máx. 500). |
| POST | `/api/v1/sync/devices` | Sincroniza lista da conta e reinicia MQTT. |
| POST | `/api/v1/printers/{printer_id}/refresh` | Atualiza cache pela cloud (+ merge MQTT se existir), grava histórico, tenta `pushall` MQTT. |

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
