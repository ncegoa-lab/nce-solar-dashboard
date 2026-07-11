NCE Solar Dashboard update: upload-time freshness for Solis/SolaX

Upload these files to the GitHub repo root, replacing existing files.

After Render redeploys, check the header build text:
Build: 2026-07-11-upload-time-freshness-v19

Changes:
- Solis and SolaX dashboard freshness now uses Mac upload/generated time, not portal row timestamp.
- This is the practical no-API-key solution.
- Upload helper warns if portal source timestamp is old, but uploads anyway.
- Past History uses today current dashboard value immediately when current data is today.
- Missing selected dates still show 0.00 kWh.
- SolaX upload command is included.

Important:
If Solis/SolaX values themselves look wrong, run the Mac upload commands after opening each portal and confirming visible values are updated.
