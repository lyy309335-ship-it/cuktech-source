# OpenClaw Integration

This optional integration exposes convenient commands for a running local CUKTECH BLE web server.

## Setup

1. Start the controller:

   ```bash
   python -m cuktech_ble_controller web
   ```

2. Configure the client environment:

   ```bash
   export CUKTECH_BASE_URL=http://127.0.0.1:8080
   export CUKTECH_API_KEY=your-local-api-key
   ```

3. Run helper commands:

   ```bash
   python integrations/openclaw/scripts/cuktech_api.py status
   python integrations/openclaw/scripts/cuktech_api.py connect
   python integrations/openclaw/scripts/cuktech_api.py mode ai
   ```

Do not store real API keys in this directory.
