Upload these files to the root of the GitHub repository:

- solar_live_app.py
- solar_generation_history.json
- upload_generation_to_render.py

Keep this file on the Mac only:
- Upload Fresh Solis To Render.command

What changed:
- Selected Plant now shows scrolling previous data:
  - Daily Date-wise
  - Weekly Week-wise
  - Yearly Year-wise
- The app records one history snapshot whenever data is refreshed or uploaded.
- The app also records one snapshot when it starts.

After redeploy, the top blue bar should show:

Build: 2026-07-10-history-v3

Important:
- Old historical dates cannot be recovered unless the vendor JSON/API already
  contains them.
- From now onward, every refresh/upload will build the history automatically.
