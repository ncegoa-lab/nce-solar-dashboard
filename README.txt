Upload these files to the root of the GitHub repository:

- solar_live_app.py
- solar_generation_history.json
- upload_generation_to_render.py
- solar_users.json

Keep this file on the Mac only:
- Upload Fresh Solis To Render.command

Do not upload:
- APP_LOGIN_DETAILS_PRIVATE.txt

Login after Render redeploy:
- Username is admin
- Password is saved locally in APP_LOGIN_DETAILS_PRIVATE.txt

This update makes the app use solar_users.json for login, so you do not need
to find NCE_APP_USER or NCE_APP_PASSWORD in Render.
