#!/bin/bash
# JarvisFeed installer for ATAS X on a Mac. The One account page gives the one line that runs this:
#   curl -fsSL '<app>/atas/install.sh?c=<code>' | bash
# It builds the JarvisFeed indicator against your own ATAS X, puts it in the ATAS indicators folder and saves the
# app URL and feed token to ~/.jarvisfeed/config, so the indicator needs no settings. It never asks for your ATAS
# password and never places orders. Safe to run again (it updates the indicator).
set -euo pipefail

APP_URL="__APP_URL__"
TOKEN="__TOKEN__"
WORK="$HOME/.jarvisfeed"

say() { printf '\033[38;5;208m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mStopped:\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$(uname)" != "Darwin" ]; then die "this installer is for a Mac. On Windows use the PowerShell line from One account."; fi
case "$TOKEN" in __*|"") die "this copy has no token. Get the install line from the One account page in the app.";; esac

say "Looking for ATAS X"
LIB=""
for d in "/Applications/ATAS X.app/Contents/MonoBundle" "$HOME/Applications/ATAS X.app/Contents/MonoBundle"; do
  if [ -f "$d/ATAS.Indicators.dll" ]; then LIB="$d"; break; fi
done
if [ -z "$LIB" ]; then
  hit=$(find /Applications "$HOME/Applications" -maxdepth 5 -name ATAS.Indicators.dll 2>/dev/null | head -1 || true)
  [ -n "$hit" ] && LIB=$(dirname "$hit")
fi
[ -n "$LIB" ] || die "ATAS X isn't installed in Applications. Install it from atas.net, open it once, then run this again."
say "Found ATAS X in $LIB"

# ATAS runs on .NET 10 today; read the version it ships with so the add-on always matches.
NET=10
cfg=$(ls "$LIB"/OFT.Platform*.runtimeconfig.json 2>/dev/null | head -1 || true)
if [ -n "$cfg" ]; then
  v=$(grep -o '"version": *"[0-9]*' "$cfg" | head -1 | grep -o '[0-9]*$' || true)
  [ -n "$v" ] && NET="$v"
fi

export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
DOTNET=""
if command -v dotnet >/dev/null 2>&1 && dotnet --list-sdks 2>/dev/null | grep -q "^$NET\."; then
  DOTNET=dotnet
elif [ -x "$WORK/dotnet/dotnet" ] && "$WORK/dotnet/dotnet" --list-sdks 2>/dev/null | grep -q "^$NET\."; then
  DOTNET="$WORK/dotnet/dotnet"
else
  say "Getting Microsoft's free .NET $NET builder (one time, about 200 MB, stays in ~/.jarvisfeed)"
  mkdir -p "$WORK"
  curl -fsSL https://dot.net/v1/dotnet-install.sh -o "$WORK/dotnet-install.sh"
  bash "$WORK/dotnet-install.sh" --channel "$NET.0" --install-dir "$WORK/dotnet" --no-path >/dev/null
  DOTNET="$WORK/dotnet/dotnet"
fi

say "Building the JarvisFeed indicator"
B="$WORK/build"
rm -rf "$B" && mkdir -p "$B"
cat > "$B/JarvisFeed.cs" <<'JARVISFEED_CS_END'
__JARVISFEED_CS__
JARVISFEED_CS_END

refs=""
for n in ATAS.Indicators ATAS.DataFeedsCore OFT.Attributes OFT.Rendering Utils.Common ATAS.Indicators.Technical; do
  [ -f "$LIB/$n.dll" ] && refs="$refs<Reference Include=\"$n\"><HintPath>$LIB/$n.dll</HintPath><Private>false</Private></Reference>"
done
cat > "$B/JarvisFeed.csproj" <<EOF
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net$NET.0-windows</TargetFramework>
    <EnableWindowsTargeting>true</EnableWindowsTargeting>
    <UseWPF>true</UseWPF>
    <AssemblyName>JarvisFeed</AssemblyName>
    <RootNamespace>AtsMatrix</RootNamespace>
    <ImplicitUsings>disable</ImplicitUsings>
    <Nullable>disable</Nullable>
    <NoWarn>CS1591;CA1416;NU1701</NoWarn>
  </PropertyGroup>
  <ItemGroup>$refs</ItemGroup>
</Project>
EOF
if ! "$DOTNET" build "$B/JarvisFeed.csproj" -c Release -o "$B/out" -nologo -v quiet > "$B/build.log" 2>&1; then
  tail -25 "$B/build.log"
  die "the build failed. Send a screenshot of the lines above in the chat and it gets fixed."
fi

DEST="$HOME/Library/Application Support/ATAS/Indicators"
if [ ! -d "$HOME/Library/Application Support/ATAS" ] && [ -d "$HOME/Library/Application Support/ATAS X" ]; then
  DEST="$HOME/Library/Application Support/ATAS X/Indicators"
fi
mkdir -p "$DEST"
cp "$B/out/JarvisFeed.dll" "$DEST/JarvisFeed.dll"
say "Installed to $DEST"

mkdir -p "$WORK"
umask 077
printf 'url=%s\ntoken=%s\n' "$APP_URL" "$TOKEN" > "$WORK/config"
chmod 600 "$WORK/config"
say "Saved the app link and token (only readable by you)"

curl -fsS -m 10 -X POST "$APP_URL/api/atas/hello" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"installer":"mac"}' >/dev/null 2>&1 && say "The app can hear this computer" || say "Couldn't reach the app just now (it may be waking up). That's OK."

printf '\n\033[38;5;208mDone.\033[0m Last step: in ATAS X open a chart (1 minute is good), Indicators, add "JarvisFeed (ATS Matrix)", OK.\n'
printf 'No settings to type. One account in the app turns LIVE within a minute or two.\n'
