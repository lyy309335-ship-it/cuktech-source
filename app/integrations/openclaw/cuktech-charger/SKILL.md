# CUKTECH Charger

Use this skill to control a local CUKTECH BLE Controller web server.

## Environment

- `CUKTECH_BASE_URL`: controller base URL, default `http://127.0.0.1:8080`
- `CUKTECH_API_KEY`: optional API key if the server enables authentication

## Commands

```bash
python scripts/cuktech_api.py status
python scripts/cuktech_api.py connect
python scripts/cuktech_api.py disconnect
python scripts/cuktech_api.py power
python scripts/cuktech_api.py mode ai
python scripts/cuktech_api.py port c1 on
python scripts/cuktech_api.py timer c1 30
```

Never place real device credentials in this skill directory.
