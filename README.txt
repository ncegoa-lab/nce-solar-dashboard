NCE Solar Dashboard update: Solis/SolaX stale protection + history fix

Upload these files to the GitHub repo root, replacing existing files.

After Render redeploys, check the header build text:
Build: 2026-07-11-history-solax-fix-v18

Changes:
- SolaX timestamp now uses actual browser capture date, not JSON rebuild date.
- Refresh Live skips stale Solis and stale SolaX captures instead of rebuilding old data.
- Upload helper blocks stale Solis uploads.
- Upload helper blocks stale SolaX uploads.
- Added Upload Fresh SolaX To Render.command for Mac.
- Internal history snapshot records as admin, so login filtering cannot block history.
- Past History API inserts today as 0.00 kWh when no today row exists.

Current local stale source check:
- Solis latest station date: 2026-07-10, today: 2026-07-11 IST.
- SolaX latest browser capture date: 2026-07-03, today: 2026-07-11 IST.

To clear Solis/SolaX stale:
Run Upload Fresh Solis To Render.command and Upload Fresh SolaX To Render.command on the Mac after each portal page shows today data.
