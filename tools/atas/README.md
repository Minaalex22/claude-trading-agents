# ATAS to ATS Matrix (JarvisFeed)

ATAS has no cloud API, so this small indicator runs inside ATAS on your computer and sends each finished
bar's order flow (delta, cumulative delta, volume, POC, big trades) to the app. It only sends data; it never
places orders. The app shows it on **One account** and feeds big trades into Atlas's order-flow check.

## What you do (once)

1. **Render**: open the ats-matrix-6la7 service > Environment > add `ATAS_TOKEN` with 32 or more random
   characters (for example, a password manager's "generate" button). Save. Render redeploys.
2. **Build the indicator** (Windows, where ATAS runs):
   - Install the free *Visual Studio Community* with ".NET desktop development".
   - New project > *Class Library* (.NET 8). Add references to `ATAS.Indicators.dll` and
     `Utils.Common.dll` from the ATAS install folder (usually `C:\Program Files (x86)\ATAS Platform`).
   - Replace the code with `JarvisFeed.cs`, Build. Copy the `.dll` from `bin\Release` to
     `Documents\ATAS\Indicators`.
3. **ATAS**: restart, open a chart (any symbol and timeframe, 1 minute is good), Indicators > *JarvisFeed (ATS Matrix)*.
   Paste the same token into **Token**. Leave **App URL** as `https://ats-matrix-6la7.onrender.com`.
4. Open **One account** in the app. The ATAS box turns **LIVE** within a bar or two.

Put it on each chart you want the agents to see (one symbol per chart). ATAS must be open for data to flow.

## If it does not show up
- ATAS box says **OFF**: `ATAS_TOKEN` isn't set on Render (or is shorter than 32 characters).
- Says **WAITING**: the indicator isn't sending. Check the token matches exactly, and the chart is getting data.
- The code was written against the public ATAS indicator API but couldn't be compiled here (no ATAS in the
  cloud). If Visual Studio shows an error, send a screenshot and it gets fixed.

## Test without ATAS
```
curl -X POST https://ats-matrix-6la7.onrender.com/api/atas/feed \
  -H "Authorization: Bearer YOUR_ATAS_TOKEN" -H "Content-Type: application/json" \
  -d '{"symbol":"ES","timeframe":"M1","bar":{"time":"t1","close":5000,"volume":1200,"delta":150}}'
```
