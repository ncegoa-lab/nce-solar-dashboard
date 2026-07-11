NCE Solar Dashboard update: offline label and stale clarification

Upload these files to the GitHub repo root, replacing the existing files.

After Render redeploys, check the header build text:
Build: 2026-07-11-offline-stale-label-v17

Changes:
- Offline plants now show OFFLINE instead of STALE, because the old date is last communication date.
- Online plants with old data still show STALE.
- Current local check shows only 2 true stale online plants remain: Solis ELVIS GOMES and Solis Manjula Nanavati, dated 2026-07-10.
- To fix those, SolisCloud must be freshly captured/uploaded from the Mac after the Solis station page shows today data.
