import asyncio
import unicodedata
from dataclasses import dataclass
from os.path import dirname, join, exists
from os import environ
from typing import Optional, Literal

from bs4 import BeautifulSoup, PageElement, Tag
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
        await asyncio.sleep(2)
        if not await _page.locator("div.user_error_report").is_visible():
            break
    await asyncio.sleep(2)
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
                # args=['--restore-last-session']
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
    isrc: Optional[str]
    detail_id: Optional[str]

    async def album_info(self, _cookie_jar: CookieJar):
        async with ClientSession(cookie_jar=_cookie_jar) as _session:
            async with _session.get(f"https://www.minc.or.jp/parts/product/detail/?album_id={self.album_id}") as _resp:
                _page_html = BeautifulSoup(await _resp.text(), 'lxml')
                tables: list[Tag] = []
                if _page_html.select("div.table_wrapper").__len__() == 1:
                    tables = _page_html.select("div.table_wrapper table")
                else:
                    for table in _page_html.select("div.table_wrapper"):
                        if not "収録曲数：0" in table.select_one("div.disk_data").get_text():
                            tables.append(table.select_one("table"))
                album_tracks: list[list[MincAlbumTrack]] = []
                for table in tables:
                    table_data = table.select("tr:not(.header)")
                    disk_tracks: list[MincAlbumTrack] = []
                    for row in table_data:
                        disk_tracks.append(MincAlbumTrack(
                            is_medley=bool(int(row.select_one("td[data-th='メドレー']").get_text(strip=True))),
                            song_title=row.select_one("td[data-th='曲名']").get_text(strip=True),
                            instrumental_or_vocal="instrumental" if
                            row.select_one("td[data-th='IV']").get_text(strip=True) == "I"
                            else "vocal",
                            artist=row.select_one("td[data-th='アーティスト']").get_text(separator="\n", strip=True),
                            isrc=row.select_one("td[data-th='ISRC']").get_text(strip=True),
                            jasrac_code=row.select_one("td[data-th='JASRAC作品コード']").get_text(strip=True)
                            if row.select_one("td[data-th='JASRAC作品コード']").get_text(strip=True) != "-" else None,
                            nextone_code=row.select_one("td[data-th='NexTone作品コード']").get_text(strip=True)
                            if row.select_one("td[data-th='NexTone作品コード']").get_text(strip=True) != "-" else None,
                            detail_id=row.select_one("td[data-th='著作権管理情報'] a")["href"].lstrip("/saku/detail/?")
                            if row.select_one("td[data-th='著作権管理情報'] a") is not None else None
                        ))
                    album_tracks.append(disk_tracks)
                return album_tracks

    async def jasrac_info(self, _cookie_jar: CookieJar) -> Optional[JasracInfo]:
        if self.detail_id is None:
            return None
        async with (ClientSession(cookie_jar=_cookie_jar) as _session):
            async with _session.get(f"https://www.minc.or.jp/saku/detail/?{self.detail_id}") as _resp:
                _page_html = BeautifulSoup(await _resp.text(), 'lxml')
                jasrac_code = _page_html.select_one("div#jasrac-area table:nth-of-type(1) tr:nth-of-type(2) td") \
                    .get_text(strip=True)
                iswc = _page_html.select_one("div#jasrac-area table:nth-of-type(1) tr:nth-of-type(3) td") \
                    .get_text(strip=True)
                lyricist = []
                composer = []
                arranger = []
                for _row in _page_html.select("div.management")[3:]:
                    name, genre = _row.select("td")
                    name = unicodedata.normalize("NFKC", name.get_text(strip=True))
                    if "作詞" in genre.get_text():
                        lyricist.append(name)
                    elif "作曲" in genre.get_text():
                        composer.append(name)
                    elif "編曲" in genre.get_text():
                        arranger.append(name)
                return JasracInfo(jasrac_code, iswc, lyricist, composer, arranger)


@dataclass
class JasracInfo:
    jasrac_code: str
    iswc: str
    lyricist: list[str]
    composer: list[str]
    arranger: list[str]


@dataclass
class MincAlbumTrack:
    is_medley: bool
    song_title: str
    instrumental_or_vocal: Literal["instrumental", "vocal"]
    artist: str
    isrc: str
    jasrac_code: Optional[str]
    nextone_code: Optional[str]
    detail_id: Optional[str]


async def search_with_isrc(_cookie_jar: CookieJar, isrc: str) -> list[MincSearchResult]:
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
                    if _row.select_one("td:nth-of-type(9)").find("button") is not None else None
                ))
            return _search_results


if __name__ == "__main__":
    cookie_jar = asyncio.run(generate_cookie_jar())
    minc_search_results = asyncio.run(search_with_isrc(cookie_jar, "JPA602100077"))
    albums = []
    for minc_search_result in minc_search_results:
        print(minc_search_result)
        print(asyncio.run(minc_search_result.jasrac_info(_cookie_jar=cookie_jar)))
        for disc in (album := asyncio.run(minc_search_result.album_info(_cookie_jar=cookie_jar))):
            for track in disc:
                print(track)
            print("\n\n")
        if album not in albums:
            albums.append(album)
    print("-------------------")
    for album in albums:
        for disc in album:
            for track in disc:
                print(track)
            print("\n\n")
        print("===================")
