# OpenClaw Integration

This optional integration exposes convenient commands for a running local CUKTECH BLE web server.
Windows only, same as the rest of the project.

## Setup

1. Start the controller:

   ```powershell
   python -m cuktech_ble_controller web
   ```

2. Configure the client environment (current PowerShell session only):

   ```powershell
   $env:CUKTECH_BASE_URL = "http://127.0.0.1:8080"
   $env:CUKTECH_API_KEY  = "your-local-api-key"
   ```

3. Run helper commands (the script lives under `cuktech-charger\scripts\`):

   ```powershell
   python integrations/openclaw/cuktech-charger/scripts/cuktech_api.py status
   python integrations/openclaw/cuktech-charger/scripts/cuktech_api.py connect
   python integrations/openclaw/cuktech-charger/scripts/cuktech_api.py mode ai
   ```

`openclaw.json.example` shows how to register the skill; the skill entry point is
`cuktech-charger/SKILL.md`.

Do not store real API keys in this directory.
