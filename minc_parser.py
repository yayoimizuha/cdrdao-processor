import asyncio
from dataclasses import dataclass
from os.path import dirname, join, exists
from os import environ
from typing import Optional

from bs4 import BeautifulSoup
from dotenv import load_dotenv, find_dotenv
from playwright.async_api import async_playwright, BrowserContext, Cookie
from aiohttp import CookieJar, ClientSession
from yarl import URL

load_dotenv(find_dotenv())


async def minc_auto_login(_chromium: BrowserContext) -> list[Cookie]:
    _page = await _chromium.new_page()
    await _page.goto("https://www.minc.or.jp/login")
    # success_login = False
    while True:
        await _page.fill(selector="input[id='mail_address']", value=environ["MINC_EMAIL"])
        await _page.fill(selector="input[id='password']", value=environ["MINC_PASSWORD"])
        while True:
            if _page.url != "https://www.minc.or.jp/login":
                break
            recaptcha_checkbox = _page.frame_locator("iframe[title='reCAPTCHA']").locator("span.recaptcha-checkbox")
            if (await recaptcha_checkbox.count()) != 0:
                recaptcha_passed = await recaptcha_checkbox.get_attribute("aria-checked", timeout=100)
                if recaptcha_passed == "true":
                    await _page.click("button[type='submit']")
                    break
            await asyncio.sleep(1)
        await asyncio.sleep(3)
        if not await _page.locator("div.user_error_report").is_visible():
            break
    await asyncio.sleep(5)
    print("Cookies:", cookies := await _page.context.cookies())
    await _page.close()
    await _chromium.close()
    return cookies


async def check_login_status() -> bool:
    if not exists(join(dirname(__file__), "minc_cookies.txt")):
        return False
    else:
        _cookie_jar = CookieJar()
        _cookie_jar.load(join(dirname(__file__), "minc_cookies.txt"))
        async with ClientSession(cookie_jar=_cookie_jar) as _session:
            async with _session.get("https://www.minc.or.jp/search", allow_redirects=False) as _resp:
                return _resp.status == 200


async def generate_cookie_jar():
    if not await check_login_status():
        async with async_playwright() as _playwright:
            user_data_dir = join(dirname(__file__), "user_data_dir")
            _browser = await _playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=['--restore-last-session']
            )

            cookies = await minc_auto_login(_browser)
            _cookie_jar = CookieJar()
            for cookie in cookies:
                _cookie_jar.update_cookies(
                    {cookie["name"]: cookie["value"]},
                    response_url=URL.build(
                        scheme="https" if cookie.get('secure') else "http",
                        host=cookie["domain"].lstrip("."),
                        path=cookie.get("path", "/")
                    )
                )
            _cookie_jar.save(join(dirname(__file__), "minc_cookies.txt"))
    else:
        _cookie_jar = CookieJar()
        _cookie_jar.load(join(dirname(__file__), "minc_cookies.txt"))

    return _cookie_jar


@dataclass
class MincSearchResult:
    title: str
    artist: str
    # lyricist: Optional[str]
    # composer: Optional[str]
    # arranger: Optional[str]
    album_name: str
    album_id: int
    first_product_number: str
    isrc: str
    detail_id: str

    def album_info(self):
        pass

    def jasrac_info(self):
        pass


async def search_with_isrc(_cookie_jar: CookieJar, isrc: str):
    async with ClientSession(cookie_jar=_cookie_jar) as _session:
        search_url = f"https://www.minc.or.jp/music/list?tr={isrc}&type=search-form-isrc"
        async with _session.get(search_url) as _resp:
            html_content = await _resp.text()
            table_html = BeautifulSoup(html_content, 'lxml').select_one("div#recorded table#track-list tbody")
            _search_results = []
            for _row in table_html.find_all("tr"):
                # _producer = dict(
                #     list(map(lambda x: tuple(x.split(": ")),
                #              _row.select_one("td:nth-of-type(4)").decode_contents().split("<br/>")))
                # )
                _search_results.append(MincSearchResult(
                    title=_row.select_one("td:nth-of-type(2)").decode_contents(),
                    artist=_row.select_one("td:nth-of-type(3)").decode_contents(),
                    album_name=_row.select_one("td:nth-of-type(6)").get_text(strip=True),
                    album_id=int(_row.select_one("td:nth-of-type(6)").find("a")["data-target"]),
                    first_product_number=_row.select_one("td:nth-of-type(5)").decode_contents().split("/")[0].strip(),
                    isrc=_row.select_one("td:nth-of-type(7)").decode_contents().strip(),
                    detail_id=_row.select_one("td:nth-of-type(9)").find("button")["data-href"]
                ))
            return _search_results


if __name__ == "__main__":
    cookie_jar = asyncio.run(generate_cookie_jar())
    print(asyncio.run(search_with_isrc(cookie_jar, "JPA600601230")))
