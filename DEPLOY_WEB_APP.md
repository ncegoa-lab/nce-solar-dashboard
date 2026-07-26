# NCE Solar Dashboard Web Deployment

This project can now run as a real web app on a cloud host such as Render,
Railway, Fly.io, or any VPS that supports Docker.

## What Works Online

- Mobile and desktop dashboard
- Password-protected access
- Admin and customer roles
- Customer plant-level access restrictions
- Plant filtering and multi-selection
- PDF generation for all plants, one plant, or selected plants
- Backend refresh for brands that support username/password scripts
- Report download links from the browser

SolisCloud is different because it may require a manual browser login or CAPTCHA.
The deployed app can show the latest imported Solis data, but fully automatic
Solis refresh needs an official Solis API key or a stable non-CAPTCHA backend
login flow.

## Recommended Host

Use Render with Docker.

1. Push this folder to a private GitHub repository.
2. Create a new Render Web Service.
3. Select Docker as the runtime.
4. Set these environment variables in Render:

```text
NCE_APP_USER=admin
NCE_APP_PASSWORD=<strong password>
NCE_SESSION_SECRET=<long random secret>
SOLAR_OUTPUT_DIR=/app/reports
SOLAR_AUTO_REFRESH_ON_OPEN=false
SEMS_USERNAME=<GoodWe username>
SEMS_PASSWORD=<GoodWe password>
FRONIUS_USERNAME=<Fronius username>
FRONIUS_PASSWORD=<Fronius password>
FIMER_USERNAME=<FIMER username>
FIMER_PASSWORD=<FIMER password>
SOLAX_USERNAME=<SolaX username>
SOLAX_PASSWORD=<SolaX password>
```

5. Deploy.
6. Open the Render URL and log in with `NCE_APP_USER` and `NCE_APP_PASSWORD`.

## Local Test Before Deployment

```bash
cd /Users/sushil/Documents/GOODWE
export NCE_APP_USER="admin"
export NCE_APP_PASSWORD="test-password"
export SOLAR_OUTPUT_DIR="$PWD/outputs/web_app"
python3 solar_live_app.py --host 0.0.0.0 --port 8765 --no-browser
```

Then open:

```text
http://127.0.0.1:8765
```

## Customer Logins

The dashboard supports two access levels:

- `admin`: can see all plants and generate all reports.
- `customer`: can see and generate reports only for assigned plants.

Plant access keys use this format:

```text
Brand::Site Name
```

Example:

```text
Solis::ELVIS GOMES
GoodWe::kunal 10kw
```

To create a local hashed user file:

```bash
python3 manage_solar_users.py customer1 --role customer --plant "Solis::ELVIS GOMES"
```

This creates `solar_users.json` with password hashes. Keep that file private.
For cloud deployment, put the same JSON content into the `NCE_USERS_JSON`
environment variable as a secret.

## Docker Test

```bash
docker build -t nce-solar-dashboard .
docker run --rm -p 8765:8765 \
  -e NCE_APP_USER=admin \
  -e NCE_APP_PASSWORD=test-password \
  -e SOLAR_OUTPUT_DIR=/app/reports \
  nce-solar-dashboard
```

## Security Notes

- Do not deploy `.solar_report_env`.
- Keep the GitHub repository private.
- Use a strong dashboard password.
- Prefer official inverter APIs for long-term cloud refresh stability.
