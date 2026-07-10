#!/bin/zsh
cd /Users/sushil/Documents/GOODWE

echo "Reset NCE Solar App login password"
echo "Username will be: admin"
echo
read -s "?Enter new app password: " APP_PASSWORD
echo
read -s "?Confirm new app password: " APP_PASSWORD_CONFIRM
echo

if [ "$APP_PASSWORD" != "$APP_PASSWORD_CONFIRM" ]; then
  echo "Passwords did not match. Nothing changed."
  read -k 1 "?Press any key to close..."
  exit 1
fi

PYTHONPYCACHEPREFIX="$PWD/.pycache" /Users/sushil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 ./manage_solar_users.py admin --role admin --password "$APP_PASSWORD"

cat > APP_LOGIN_DETAILS_PRIVATE.txt <<EOF
NCE Solar App Login

Username: admin
Password: $APP_PASSWORD

Upload solar_users.json to GitHub after changing this password.
Do not upload this private password file.
EOF
chmod 600 APP_LOGIN_DETAILS_PRIVATE.txt

echo
echo "Password reset complete."
echo "Now upload the new solar_users.json to GitHub root and redeploy Render."
read -k 1 "?Press any key to close..."
