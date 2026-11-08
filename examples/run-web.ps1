$ErrorActionPreference = "Stop"

if (-not $env:CUKTECH_DEVICE_MAC) {
  Write-Warning "CUKTECH_DEVICE_MAC is not set. Copy .env.example (repo root) to .env and fill in your own values first."
}

python -m cuktech_ble_controller web
