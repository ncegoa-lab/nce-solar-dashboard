#!/bin/zsh
cd /Users/sushil/Documents/GOODWE

echo "Refreshing Solis on this Mac and uploading to Render..."
echo "If Solis asks for CAPTCHA, complete it in the browser window."

PYTHONPYCACHEPREFIX="$PWD/.pycache" .venv/bin/python ./upload_generation_to_render.py --brand solis
RESULT=$?

echo
if [ "$RESULT" -eq 0 ]; then
  echo "Done. You can close this window."
else
  echo "Solis upload did not complete. Please read the message above."
fi
read -k 1 "?Press any key to close..."
