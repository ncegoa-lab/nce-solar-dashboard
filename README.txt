NCE Solar Dashboard update: Solis stale refresh + history zero fix

Upload these files to the GitHub repo root, replacing the existing files.

After Render redeploys, check the header build text:
Build: 2026-07-11-solis-history-fix-v16

Changes:
- Solis refresh will no longer convert an old browser capture and pretend it refreshed.
- Refresh log now says Solis refresh skipped when the Solis capture is stale.
- Mac Solis upload tool checks the Solis station data date before upload.
- If Solis data is still old, upload stops and says the latest Solis date found.
- Past History date picker opens on today IST.
- If a selected daily date has no data, Daily shows 0.00 kWh instead of showing yesterday.

Important:
For Solis, the web app cannot refresh SolisCloud by itself without official API access. Use Upload Fresh Solis To Render.command on the Mac after SolisCloud station page shows today data.
