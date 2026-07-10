Upload these files to the root of the GitHub repository:

- solar_live_app.py
- solar_users.json
- manage_solar_users.py

Keep this file on the Mac only:
- Reset App Login Password.command

What changed:
- The app now prefers solar_users.json over old Render user variables.
- This prevents stale Render login settings from overriding the uploaded password.

After Render redeploys, the top blue bar should show:

Build: 2026-07-10-login-file-priority-v7

Login:
- Username: admin
- Password: the password saved in APP_LOGIN_DETAILS_PRIVATE.txt
