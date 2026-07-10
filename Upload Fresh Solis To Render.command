#!/bin/zsh
cd /Users/sushil/Documents/GOODWE

echo "Refreshing Solis on this Mac and uploading to Render..."
echo "If Solis asks for CAPTCHA, complete it in the browser window."

PYTHONPYCACHEPREFIX="$PWD/.pycache" .venv/bin/python ./upload_generation_to_render.py --brand solis

echo
echo "Done. You can close this window."
read -k 1 "?Press any key to close..."
