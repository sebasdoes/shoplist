"""Reproduce the user's sign-in click in a real Chromium and capture everything."""
import asyncio
from playwright.async_api import async_playwright

URL = "https://shoplist.v.akiha.nl"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        console, errors = [], []

        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: console.append(f"[requestfailed] {r.url} {r.failure}"))

        await page.goto(URL, wait_until="networkidle", timeout=30000)
        print("landed url:", page.url)
        print("title:", await page.title())

        btn = page.locator("button.btn-primary")
        print("button count:", await btn.count(), "| text:", await btn.first.text_content() if await btn.count() else None)

        if await btn.count():
            await btn.first.click()
            # navigation may or may not happen; give the flow time
            try:
                await page.wait_for_url("**id.akiha.nl/**", timeout=8000)
                print("NAVIGATED to SSO:", page.url)
            except Exception:
                print("no navigation to SSO within 8s")
            await page.wait_for_timeout(3000)
            print("final url after click:", page.url)
            html = await page.content()
            s = " ".join(html[:1200].split())
            print("page snippet after click:", s[:600])

        print("\n--- console messages ---")
        for c in console[:30]:
            print(c)
        print("\n--- page errors ---")
        for e in errors[:10]:
            print(e)
        await browser.close()


asyncio.run(main())
