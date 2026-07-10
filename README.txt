Upload these files to the root of the GitHub repository:

- solar_live_app.py
- upload_generation_to_render.py
- solis_generation.json
- solax_generation.json

What this adds:
- Render gets a secure upload endpoint: /api/upload-generation
- Your Mac can refresh Solis/SolaX and upload only the safe generation JSON
- No GitHub redeploy is needed every time Solis is refreshed
- Browser capture/session files stay on the Mac

Render environment variable required:
- SOLAR_UPLOAD_TOKEN

Set SOLAR_UPLOAD_TOKEN to a long private value. Use the same value on the Mac
when running the uploader.

Mac command for fresh Solis upload:

cd /Users/sushil/Documents/GOODWE
export RENDER_APP_URL="https://YOUR-RENDER-APP.onrender.com"
export SOLAR_UPLOAD_TOKEN="same private value used in Render"
export SOLIS_USERNAME="your Solis username"
export SOLIS_PASSWORD="your Solis password"
PYTHONPYCACHEPREFIX="$PWD/.pycache" .venv/bin/python ./upload_generation_to_render.py --brand solis

If the Solis browser is already freshly captured and you only want to upload:

PYTHONPYCACHEPREFIX="$PWD/.pycache" .venv/bin/python ./upload_generation_to_render.py --brand solis --skip-capture
