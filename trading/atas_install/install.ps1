# JarvisFeed installer for ATAS on Windows (classic ATAS and ATAS X). The One account page gives the one line:
#   powershell -ExecutionPolicy Bypass -c "irm '<app>/atas/install.ps1?c=<code>' | iex"
# It builds the JarvisFeed indicator against your own ATAS, puts it in the ATAS indicators folder and saves the app
# URL and feed token to %USERPROFILE%\.jarvisfeed\config, so the indicator needs no settings. It never asks for
# your ATAS password and never places orders. Safe to run again (it updates the indicator).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$AppUrl = '__APP_URL__'
$Token = '__TOKEN__'
$Work = Join-Path $env:USERPROFILE '.jarvisfeed'

function Say($m) { Write-Host "==> $m" -ForegroundColor DarkYellow }
function Stop-Install($m) { Write-Host "Stopped: $m" -ForegroundColor Red; throw $m }

if ($Token -like '__*' -or -not $Token) { Stop-Install 'this copy has no token. Get the install line from the One account page in the app.' }

Say 'Looking for ATAS'
$installs = @()
$candidates = @(
  @{ Dir = (Join-Path $env:ProgramFiles 'ATAS X'); Dest = (Join-Path $env:APPDATA 'ATAS X\Indicators') },
  @{ Dir = (Join-Path ${env:ProgramFiles(x86)} 'ATAS Platform'); Dest = (Join-Path $env:APPDATA 'ATAS\Indicators') },
  @{ Dir = (Join-Path $env:ProgramFiles 'ATAS Platform'); Dest = (Join-Path $env:APPDATA 'ATAS\Indicators') }
)
foreach ($c in $candidates) { if ($c.Dir -and (Test-Path (Join-Path $c.Dir 'ATAS.Indicators.dll'))) { $installs += $c } }
if (-not $installs) {
  $hit = Get-ChildItem -Path $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA -Filter ATAS.Indicators.dll -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($hit) {
    $dest = if ($hit.DirectoryName -like '*ATAS X*') { Join-Path $env:APPDATA 'ATAS X\Indicators' } else { Join-Path $env:APPDATA 'ATAS\Indicators' }
    $installs += @{ Dir = $hit.DirectoryName; Dest = $dest }
  }
}
if (-not $installs) { Stop-Install "ATAS isn't installed. Install it from atas.net, open it once, then run this again." }
$Lib = $installs[0].Dir
Say "Found ATAS in $Lib"

# ATAS runs on .NET 10 today; read the version it ships with so the add-on always matches.
$Net = 10
$cfg = Get-ChildItem -Path $Lib -Filter 'OFT.Platform*.runtimeconfig.json' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cfg) {
  $m = [regex]::Match((Get-Content $cfg.FullName -Raw), '"version"\s*:\s*"(\d+)')
  if ($m.Success) { $Net = [int]$m.Groups[1].Value }
}

$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'; $env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$Dotnet = $null
$sys = Get-Command dotnet -ErrorAction SilentlyContinue
if ($sys -and ((& dotnet --list-sdks 2>$null) -match "^$Net\.")) { $Dotnet = 'dotnet' }
elseif ((Test-Path "$Work\dotnet\dotnet.exe") -and ((& "$Work\dotnet\dotnet.exe" --list-sdks 2>$null) -match "^$Net\.")) { $Dotnet = "$Work\dotnet\dotnet.exe" }
else {
  Say "Getting Microsoft's free .NET $Net builder (one time, about 200 MB, stays in $Work)"
  New-Item -ItemType Directory -Force -Path $Work | Out-Null
  Invoke-WebRequest -UseBasicParsing 'https://dot.net/v1/dotnet-install.ps1' -OutFile "$Work\dotnet-install.ps1"
  & "$Work\dotnet-install.ps1" -Channel "$Net.0" -InstallDir "$Work\dotnet" -NoPath | Out-Null
  $Dotnet = "$Work\dotnet\dotnet.exe"
}

Say 'Building the JarvisFeed indicator'
$B = Join-Path $Work 'build'
if (Test-Path $B) { Remove-Item -Recurse -Force $B }
New-Item -ItemType Directory -Force -Path $B | Out-Null
$source = @'
__JARVISFEED_CS__
'@
Set-Content -Path "$B\JarvisFeed.cs" -Value $source -Encoding UTF8

$refs = ''
foreach ($n in 'ATAS.Indicators', 'ATAS.DataFeedsCore', 'OFT.Attributes', 'OFT.Rendering', 'Utils.Common', 'ATAS.Indicators.Technical') {
  $p = Join-Path $Lib "$n.dll"
  if (Test-Path $p) { $refs += "<Reference Include=`"$n`"><HintPath>$p</HintPath><Private>false</Private></Reference>" }
}
$proj = @"
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net$Net.0-windows</TargetFramework>
    <UseWPF>true</UseWPF>
    <AssemblyName>JarvisFeed</AssemblyName>
    <RootNamespace>AtsMatrix</RootNamespace>
    <ImplicitUsings>disable</ImplicitUsings>
    <Nullable>disable</Nullable>
    <NoWarn>CS1591;CA1416;NU1701</NoWarn>
  </PropertyGroup>
  <ItemGroup>$refs</ItemGroup>
</Project>
"@
Set-Content -Path "$B\JarvisFeed.csproj" -Value $proj -Encoding UTF8
& $Dotnet build "$B\JarvisFeed.csproj" -c Release -o "$B\out" -nologo -v quiet *> "$B\build.log"
if ($LASTEXITCODE -ne 0) {
  Get-Content "$B\build.log" -Tail 25
  Stop-Install 'the build failed. Send a screenshot of the lines above in the chat and it gets fixed.'
}

foreach ($i in $installs) {
  New-Item -ItemType Directory -Force -Path $i.Dest | Out-Null
  Copy-Item "$B\out\JarvisFeed.dll" (Join-Path $i.Dest 'JarvisFeed.dll') -Force
  Say "Installed to $($i.Dest)"
}

New-Item -ItemType Directory -Force -Path $Work | Out-Null
Set-Content -Path "$Work\config" -Value "url=$AppUrl`ntoken=$Token" -Encoding ASCII
Say 'Saved the app link and token in your user folder'

try {
  Invoke-RestMethod -Method Post -TimeoutSec 10 -Uri "$AppUrl/api/atas/hello" -Headers @{ Authorization = "Bearer $Token" } `
    -ContentType 'application/json' -Body '{"installer":"windows"}' | Out-Null
  Say 'The app can hear this computer'
} catch { Say "Couldn't reach the app just now (it may be waking up). That's OK." }

Write-Host ''
Write-Host 'Done. Last step: restart ATAS, open a chart (1 minute is good), Indicators, add "JarvisFeed (ATS Matrix)", OK.' -ForegroundColor DarkYellow
Write-Host 'No settings to type. One account in the app turns LIVE within a minute or two.'
