Upload these files to the root of the GitHub repository:

- solar_live_app.py
- solar_users.json
- solar_generation_history.json
- upload_generation_to_render.py
- manage_solar_users.py

Keep these files on the Mac only:
- Reset App Login Password.command
- Upload Fresh Solis To Render.command

What changed:
- Selected Report now generates only ticked plants.
- No plants are ticked by default.
- All Plants Report is separate and explicit.
- Plant Report uses the tapped/open plant.
- PDF reports open in an app viewer with:
  - Back to App
  - Download
  - Share
  - Print

After Render redeploys, top blue bar should show:

Build: 2026-07-10-report-viewer-v13
