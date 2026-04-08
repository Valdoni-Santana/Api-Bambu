# Plano de testes — bambu-bridge

## Objetivo

Validar o microserviço `bambu-bridge` antes de integrar ao NerdGeek: autenticação Bambu, persistência, MQTT/polling, REST e snapshots.

## Pré-requisitos

- Python 3.11+
- `pip install -r requirements.txt -r requirements-bridge.txt`
- Variáveis de ambiente conforme `.env.example` (token Bambu válido ou usuário/senha sem 2FA interativo no container)
- `PYTHONPATH` apontando para o diretório `app` do repositório

## Testes automáticos rápidos

| ID | Descrição | Como executar | Resultado esperado |
|----|-----------|---------------|-------------------|
| S1 | Health | `curl -s http://127.0.0.1:8010/health` | `api: ok`, `db: ok`, contagens coerentes |
| S2 | Lista impressoras | `GET /api/v1/printers` com Bearer | 200, array com `device_id`, `online` |
| S3 | Detalhe | `GET /api/v1/printers/{id}` | 200, campos de status preenchidos após MQTT/poll |
| S4 | Painel status | `GET /api/v1/printers/{id}/status` | 200, schema estável NerdGeek |
| S5 | AMS | `GET /api/v1/printers/{id}/ams` | 200, `has_ams`, `slots` |
| S6 | Snapshot | `GET .../camera/snapshot?refresh=true` | 200 JPEG ou 503 com mensagem clara |
| S7 | Histórico | `GET .../history?limit=20` | 200, até 100 entradas configurável |
| S8 | Sync manual | `POST /api/v1/sync/devices` | 200, `synced` >= 0 |

Script agregado: `python scripts/smoke_test.py --base http://127.0.0.1:8010 --token <API_TOKEN>`.

## Testes manuais recomendados

1. **Token expirado**: invalidar token e reiniciar — `bambu_auth: fail` no `/health`, logs sem vazamento de senha.
2. **MQTT indisponível**: bloquear saída para `us.mqtt.bambulab.com` — após `MQTT_STALE_SECONDS`, polling cloud deve atualizar cache quando a API responder.
3. **Câmera**: sem `BAMBU_PRINTER_HOST_MAP` e sem URL cloud — snapshot deve retornar erro tratado (503/404 conforme endpoint).
4. **Docker**: `docker compose up -d`, healthcheck do container verde, volumes `storage` com `.db` e `.jpg`.

## Critérios de aceite

- Nenhuma senha Bambu em logs.
- API protegida com `Authorization: Bearer` quando `API_TOKEN` está definido.
- Documentação `README_NERDGEEK.md` seguida para primeira autenticação com 2FA via CLI.
