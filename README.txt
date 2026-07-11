Upload these files to GitHub, replacing the files with the same names:

solar_live_app.py
upload_generation_to_render.py
fimer_backend_export_generation.py
solis_api_export_generation.py
solax_api_export_generation.py
.solar_report_env.example

Do not upload .solar_report_env. It contains private credentials.

Render environment variables to add:

SOLIS_API_BASE
SOLIS_KEY_ID
SOLIS_KEY_SECRET
SOLAX_API_BASE
SOLAX_TOKEN_ID
FIMER_API_PLANT_IDS

Optional for SolaX public API:

SOLAX_DEVICE_SNS

Use SOLAX_DEVICE_SNS only if SolaX API still says "no auth". Format:

Plant Name=INVERTER_SERIAL,Another Plant=INVERTER_SERIAL
