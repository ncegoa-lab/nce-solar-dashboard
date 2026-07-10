Upload these files to the root of the GitHub repository:

- solar_live_app.py
- upload_generation_to_render.py
- solis_generation.json
- solax_generation.json

Keep this file on the Mac only:
- Upload Fresh Solis To Render.command

What this adds:
- Render gets a secure upload endpoint: /api/upload-generation
- Your Mac can refresh Solis/SolaX and upload only the safe generation JSON
- No GitHub redeploy is needed every time Solis is refreshed
- Browser capture/session files stay on the Mac
- The dashboard top blue bar will show:
  Build: 2026-07-10-solis-mac-upload-v2

If that Build line is not visible after redeploy, Render is still running old
code and Solis upload will not work yet.

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

For repeated use, set RENDER_APP_URL and SOLAR_UPLOAD_TOKEN in:

/Users/sushil/Documents/GOODWE/.solar_report_env

Then double-click:

Upload Fresh Solis To Render.command
