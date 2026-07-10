Upload these files to the root of the GitHub repository:

- solar_live_app.py
- solar_generation_history.json
- upload_generation_to_render.py

Keep this file on the Mac only:
- Upload Fresh Solis To Render.command

What changed:
- Selected Plant now has user-selectable history controls:
  - Daily Date selector
  - Week selector
  - Year selector
- The selected value is shown immediately above the scrolling history table.

After Render redeploys, the top blue bar should show:

Build: 2026-07-10-history-selectors-v4

History grows from now onward whenever the dashboard refreshes or fresh Solis
data is uploaded from the Mac.
