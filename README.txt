NCE Solar Dashboard update: Solis direct fetch without API key

Upload these files to the GitHub repo root, replacing existing files.

After Render redeploys, check the header build text:
Build: 2026-07-11-solis-direct-fetch-v20

What this fixes:
- Solis Mac capture now records station-list request details.
- Before saving, it directly re-fetches https://v3.soliscloud.com/api/station/list from the logged-in browser session.
- No API key required; it uses the browser login/cookies.
- Converter prefers the direct re-fetch response over passive page data.
- This avoids depending only on the SolisCloud page visually refreshing.

How to use:
1. Upload these files and redeploy Render.
2. On the Mac, double-click Upload Fresh Solis To Render.command.
3. If Solis asks for CAPTCHA, solve it.
4. Keep the station page open until the command finishes.

Note:
If SolisCloud itself returns old generation values from the endpoint, no no-key method can invent new values. But this is the strongest no-API-key capture method because it calls the same authenticated endpoint directly.
