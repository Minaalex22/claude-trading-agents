const test = require("node:test");
const assert = require("node:assert");
const { PAGES, isInternal, normalizeServer, DEFAULT_SERVER } = require("../pages");

test("server address is cleaned up", () => {
  assert.strictEqual(normalizeServer(""), DEFAULT_SERVER);
  assert.strictEqual(normalizeServer("my-app.onrender.com/"), "https://my-app.onrender.com");
  assert.strictEqual(normalizeServer("http://127.0.0.1:8080/news"), "http://127.0.0.1:8080");
});

test("own pages stay in the app, other sites go to the browser", () => {
  assert.ok(isInternal(DEFAULT_SERVER + "/trades", DEFAULT_SERVER));
  assert.ok(!isInternal("https://polymarket.com", DEFAULT_SERVER));
  assert.ok(!isInternal("not a url", DEFAULT_SERVER));
});

test("every page has a path and the menu shortcuts are unique", () => {
  const keys = PAGES.filter((p) => p.key).map((p) => p.key);
  assert.strictEqual(new Set(keys).size, keys.length);
  for (const p of PAGES) assert.ok(p.path.startsWith("/"));
});
