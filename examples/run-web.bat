@echo off
setlocal

if "%CUKTECH_DEVICE_MAC%"=="" (
  echo CUKTECH_DEVICE_MAC is not set. Configure your environment first.
)

python -m cuktech_ble_controller web
