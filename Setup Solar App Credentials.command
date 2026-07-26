#!/bin/zsh
cd /Users/sushil/Documents/GOODWE
ENV_FILE=".solar_report_env"

echo "This will save portal credentials locally for the Solar Live App."
echo "File: $PWD/$ENV_FILE"
echo

read "SEMS_USERNAME?GoodWe/SEMS username: "
read -s "SEMS_PASSWORD?GoodWe/SEMS password: "
echo
read "FRONIUS_USERNAME?Fronius username: "
read -s "FRONIUS_PASSWORD?Fronius password: "
echo
read "FIMER_USERNAME?FIMER username: "
read -s "FIMER_PASSWORD?FIMER password: "
echo
read "SOLIS_USERNAME?Solis username (optional): "
read -s "SOLIS_PASSWORD?Solis password (optional): "
echo
read "SOLAX_USERNAME?SolaX username (optional): "
read -s "SOLAX_PASSWORD?SolaX password (optional): "
echo

cat > "$ENV_FILE" <<EOF
SEMS_USERNAME="$SEMS_USERNAME"
SEMS_PASSWORD="$SEMS_PASSWORD"
FRONIUS_USERNAME="$FRONIUS_USERNAME"
FRONIUS_PASSWORD="$FRONIUS_PASSWORD"
FIMER_USERNAME="$FIMER_USERNAME"
FIMER_PASSWORD="$FIMER_PASSWORD"
SOLIS_USERNAME="$SOLIS_USERNAME"
SOLIS_PASSWORD="$SOLIS_PASSWORD"
SOLAX_USERNAME="$SOLAX_USERNAME"
SOLAX_PASSWORD="$SOLAX_PASSWORD"
EOF

chmod 600 "$ENV_FILE"
echo
echo "Saved credentials for Solar Live App."
echo "You can now double-click: Run Solar Live App.command"
echo
read "DONE?Press Enter to close..."
