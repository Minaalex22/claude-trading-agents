# ATAS to ATS Matrix (JarvisFeed)

ATAS has no cloud API, so this small indicator runs inside ATAS on your computer and sends each finished
bar's order flow (delta, cumulative delta, volume, POC, big trades) to the app. It only sends data; it never
places orders. The app shows it on **One account** and feeds big trades into Atlas's order-flow check.

## What you do (once, about two minutes)

1. In the app open **One account** and tap **Connect ATAS**. Copy the line for your computer (Mac or Windows).
2. Paste it in **Terminal** (Mac) or **PowerShell** (Windows) and press Enter. It finds your ATAS (ATAS X on a Mac,
   classic ATAS or ATAS X on Windows), gets Microsoft's free .NET builder if needed, builds the indicator against
   your own ATAS, copies it into the ATAS indicators folder and saves the app link and token in `~/.jarvisfeed/config`.
3. In ATAS open a chart (1 minute is good), **Indicators**, add **JarvisFeed (ATS Matrix)**, OK. Nothing to type.
   The ATAS box on One account turns **LIVE** within a minute or two.

No Render setup is needed: without `ATAS_TOKEN`, the feed token is derived from `DASHBOARD_PASSWORD` (if you change
that password, run the install line again). The install line works for 30 minutes and never contains the password.
The indicator source is `trading/atas_install/JarvisFeed.cs`; the installer scripts sit next to it.

## If it does not show up
- ATAS box says **OFF**: neither `DASHBOARD_PASSWORD` nor `ATAS_TOKEN` is set on Render.
- Says **WAITING**: the indicator isn't on a chart yet, or the chart has no data. Run the install line again if you changed the password.
- If the installer says the build failed, send a screenshot of the lines it prints and it gets fixed.

## Test without ATAS
```
curl -X POST https://ats-matrix-6la7.onrender.com/api/atas/feed \
  -H "Authorization: Bearer $(grep token ~/.jarvisfeed/config | cut -d= -f2)" -H "Content-Type: application/json" \
  -d '{"symbol":"ES","timeframe":"M1","bar":{"time":"t1","close":5000,"volume":1200,"delta":150}}'
```
