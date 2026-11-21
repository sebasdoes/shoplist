"""Reproduce the signed-in UI render headlessly with a forged session.
The API is mocked (200s), so the app should render the LIST view. If any
component throws during that render, we catch the pageerror and the DOM stays
on the splash — the exact device symptom."""
import asyncio
from playwright.async_api import async_playwright

URL = "https://shoplist.v.akiha.nl"

FORGE = """
const req = indexedDB.open('shoplist', 1);
req.onupgradeneeded = (e) => {
  const db = e.target.result;
  if (!db.objectStoreNames.contains('items'))
    db.createObjectStore('items', { keyPath: 'id' }).createIndex('by_rev', 'rev');
  if (!db.objectStoreNames.contains('history'))
    db.createObjectStore('history', { keyPath: 'id' }).createIndex('by_occurred_at', 'occurred_at');
  if (!db.objectStoreNames.contains('outbox'))
    db.createObjectStore('outbox', { keyPath: 'seq', autoIncrement: true });
  if (!db.objectStoreNames.contains('state'))
    db.createObjectStore('state');
};
req.onsuccess = () => {
  const db = req.result;
  const tx = db.transaction('state', 'readwrite');
  tx.objectStore('state').put(
    { access_token: 'forged', refresh_token: null, id_token: null,
      exp: Math.floor(Date.now()/1000) + 3600, sub: 'test-sub', name: 'Test' },
    'session');
};
"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        async def mock_api(route):
            path = route.request.url
            body = "{}"
            if "/sync/pull" in path:
                body = '{"watermark":1,"items":[],"history":[],"full_snapshot":false}'
            elif "/sync/push" in path:
                body = '{"applied":[],"trip":{"duplicate":false,"readded":[]}}'
            elif "/history" in path:
                body = "[]"
            else:
                body = '{"ok":true}'
            await route.fulfill(status=200, content_type="application/json", body=body)

        await context.route("**/api.v1/**", lambda r: asyncio.ensure_future(mock_api(r)))
        # route pattern with dot escaping issue: use generic
        await context.unroute_all()
        await context.route("**/api/**", lambda r: asyncio.ensure_future(mock_api(r)))

        page = await context.new_page()
        errors, console = [], []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)

        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.evaluate(FORGE.replace("indexedDB.open", "__wrap"))  # noop placeholder, see below
        await browser.close()
        return


async def run2():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        async def mock_api(route):
            path = route.request.url
            if "/sync/pull" in path:
                body = '{"watermark":1,"items":[],"history":[],"full_snapshot":false}'
            elif "/sync/push" in path:
                body = '{"applied":[],"trip":{"duplicate":false,"readded":[]}}'
            elif "/history" in path:
                body = "[]"
            else:
                body = '{"ok":true}'
            await route.fulfill(status=200, content_type="application/json", body=body)

        await context.route("**/*/api/**", mock_api)

        page = await context.new_page()
        errors, console = [], []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}") if m.type in ("error",) else None)

        await page.goto("about:blank")
        # forge the session on the real origin first
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.evaluate(
            """async () => {
              await new Promise((resolve) => {
                const req = indexedDB.open('shoplist', 1);
                req.onupgradeneeded = (e) => {
                  const db = e.target.result;
                  if (!db.objectStoreNames.contains('items'))
                    db.createObjectStore('items', { keyPath: 'id' }).createIndex('by_rev', 'rev');
                  if (!db.objectStoreNames.contains('history'))
                    db.createObjectStore('history', { keyPath: 'id' }).createIndex('by_occurred_at', 'occurred_at');
                  if (!db.objectStoreNames.contains('outbox'))
                    db.createObjectStore('outbox', { keyPath: 'seq', autoIncrement: true });
                  if (!db.objectStoreNames.contains('state'))
                    db.createObjectStore('state');
                };
                req.onsuccess = () => {
                  const db = req.result;
                  const tx = db.transaction('state', 'readwrite');
                  tx.objectStore('state').put(
                    { access_token: 'forged', refresh_token: null, id_token: null,
                      exp: Math.floor(Date.now()/1000) + 3600, sub: 'test-sub', name: 'Test' },
                    'session');
                  tx.oncomplete = resolve;
                };
              });
            }"""
        )
        # second load WITH session present; app should auth-resolve -> signedIn
        await page.goto(URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(4000)
        splash = await page.locator(".center-screen").count()
        header = await page.locator(".app-header").count()
        addbar = await page.locator("text=Add").count()
        btn = await page.locator("button.btn-primary").count()
        print("after load: splash-branch count:", splash, "| app-header:", header, "| add-ish:", addbar, "| signin-btn:", btn)
        print("page url:", page.url)
        body = " ".join((await page.content())[:1500].split())
        print("body:", body[:700])
        print("--- console errors:", console[:10])
        print("--- page errors:", errors[:10])
        await browser.close()


asyncio.run(run2())
