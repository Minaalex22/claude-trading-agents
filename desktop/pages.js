// The pages of the trading app, shown in the desktop app's menu. Paths match trading/dashboard.py.
const DEFAULT_SERVER = "https://ats-matrix-6la7.onrender.com";

const PAGES = [
  { label: "News", path: "/news", key: "1" },
  { label: "News Pipeline", path: "/pipeline", key: "2" },
  { label: "Trades", path: "/trades", key: "3" },
  { label: "Wallet", path: "/wallet" },
  { label: "Calculator", path: "/calculator" },
  { label: "Decision", path: "/decision" },
  { label: "Desk", path: "/", key: "4" },
  { label: "One account", path: "/account", key: "0" },
  { label: "Orbit portfolio", path: "/orbit" },
  { label: "Session desks", path: "/desks" },
  { label: "Session brief", path: "/brief" },
  { label: "Time Machine", path: "/timemachine" },
  { label: "News Lab", path: "/newslab" },
  { label: "Mitrade", path: "/mitrade" },
  { label: "BTCC", path: "/btcc" },
  { label: "Markets", path: "/markets", key: "5" },
  { label: "Crypto", path: "/crypto" },
  { label: "Forex", path: "/forex" },
  { label: "Gold & Commodities", path: "/commodities" },
  { label: "Indices", path: "/indices" },
  { label: "Shares", path: "/shares" },
  { label: "Polymarket", path: "/polymarket", key: "6" },
  { label: "Order flow", path: "/orderflow", key: "7" },
  { label: "Agents", path: "/conductor", key: "8" },
  { label: "Pairs trading", path: "/pairs" },
  { label: "Galaxy", path: "/galaxy" },
  { label: "3D view", path: "/3d" },
  { label: "JARVIS", path: "/jarvis", key: "9" },
];

// Same server as the app (stays inside the window) or an outside site (opens in the normal browser).
function isInternal(url, server) {
  try {
    return new URL(url).origin === new URL(server).origin;
  } catch {
    return false;
  }
}

function normalizeServer(value) {
  if (!value) return DEFAULT_SERVER;
  let v = String(value).trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(v)) v = "https://" + v;
  try {
    const u = new URL(v);
    return u.origin;
  } catch {
    return DEFAULT_SERVER;
  }
}

module.exports = { DEFAULT_SERVER, PAGES, isInternal, normalizeServer };
