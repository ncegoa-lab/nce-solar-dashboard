NCE Solar Dashboard update: refresh whenever app opens

Upload these files to GitHub repo root, replacing existing files.

After Render redeploys, check the header build text:
Build: 2026-07-11-refresh-on-open-v22

Change:
- Every time the dashboard page is opened, it queues Refresh Live automatically.
- If a refresh is already running, it will not start a duplicate refresh.
- Startup auto-refresh is still kept.

Note:
Solis/SolaX still require Mac upload commands for true portal data capture because Render cannot log into those portals without API access.
