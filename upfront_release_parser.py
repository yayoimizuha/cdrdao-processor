import more_itertools
import re
import sys
import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Optional, Literal
from urllib.parse import urljoin
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from pandas import DataFrame


@dataclass
class Release:
    title: str
    artist: str
    release_date: date
    label: str
    release_type: Literal["single", "album"]
    packages: list[Package]


@dataclass
class Package:
    disc_type: Optional[str]
    discs: list[Disc]


@dataclass
class Disc:
    disc_number: str
    tracks: list[Track]


@dataclass
class Track:
    order: int
    title: str
    lyricist: Optional[str]
    composer: Optional[str]
    arranger: Optional[str]
    singer: Optional[str]


async def hello_project_release_parser() -> list[Release]:
    async with (ClientSession() as _session):
        _releases = []
        for _release_type in ("single", "album"):
            async with _session.get(f"https://helloproject.com/release/search/?g={_release_type}") as _resp:
                release_counts = int(re.search(
                    r"\((\d*?)\)",
                    BeautifulSoup(await _resp.text(), "lxml").select_one("div#sub_navi li.active").get_text()
                ).group(1))
            _detail_pages = []
            for _page_order in range(1, (release_counts // 20) + 2):
                async with _session.get(
                        f"https://helloproject.com/release/search/?g={_release_type}&p={_page_order}"
                ) as _resp:
                    for _container in BeautifulSoup(await _resp.text(), "lxml").select("section"):
                        if _container.select_one("img")["src"] == \
                                "https://cdn.helloproject.com/img/release/s/nowprinting.jpg":
                            continue
                        _detail_pages.append(
                            urljoin(_resp.url.__str__(), _container.select_one("a.link_block")["href"])
                        )
            _page_contents = []
            _sem = asyncio.Semaphore(10)

            async def _fetch_detail_page(_detail_page: str):
                async with _sem:
                    async with _session.get(_detail_page) as __resp:
                        return await __resp.text()

            for _detail_page in _detail_pages:
                _page_contents.append(_fetch_detail_page(_detail_page))
            for _page_content in await asyncio.gather(*_page_contents):
                # noinspection PyTypeChecker
                _releases.append(Release(
                    title="",
                    artist="",
                    release_date=date.fromisocalendar(1970, 1, 1),
                    label="",
                    release_type=_release_type,
                    packages=[]

                ))
                for block in BeautifulSoup(_page_content, "lxml").select_one("div#rd_right").find_all(recursive=False):
                    match block.name:
                        case "h2":
                            _releases[-1].title = block.get_text(strip=True)
                        case "p":
                            _releases[-1].artist = block.get_text(strip=True)
                        case "table":
                            if not "CD" in block.select_one("th").get_text():
                                continue
                            _tracks = []
                            for _i, _row in enumerate(list(more_itertools.chunked(block.select("tr"), n=2))[1:], start=1):
                                row_1 = _row[0].select("td")
                                row_2 = _row[1].select("td")
                                _tracks.append(Track(
                                    order=_i,
                                    title=row_1[1].get_text(strip=True),
                                    lyricist=row_1[3].get_text(strip=True),
                                    composer=row_1[4].get_text(strip=True),
                                    arranger=row_1[5].get_text(strip=True),
                                    singer=row_2[0].get_text(strip=True).removeprefix("歌：")
                                ))
                            _releases[-1].packages[-1].discs.append(Disc(
                                disc_number=f"{_pn_alpha}-{_pn_serial}",
                                tracks=_tracks
                            ))
                            _pn_serial += 1
                        case "div":
                            match list(block.attrs.values())[0]:
                                case "table_wrapper":
                                    _data = [_row.select("td")[1].get_text(strip=True) for _row in
                                             block.select("table#typeA tr")]
                                    _release_date = date.strptime(_data[1], "%Y/%m/%d")
                                    _release_label = _data[2]
                                    _releases[-1].release_date = _release_date
                                    _releases[-1].label = _release_label
                                case ["release_edition"]:
                                    _data = block.get_text(strip=True)
                                    if (_disc_type := re.search("【(.*?)】", _data)) is not None:
                                        _disc_type = _disc_type.group(1)
                                    _pn = re.search("([A-Z]{3,4})-([0-9]{,5})", _data).groups()
                                    _pn_alpha = _pn[0]
                                    _pn_serial = int(_pn[1])
                                    _releases[-1].packages.append(Package(
                                        disc_type=_disc_type,
                                        discs=[]
                                    ))
                        case _:
                            print("Unhandled block:", block.name, block.get("class"), file=sys.stderr)
                            pass
        return _releases


async def upfront_works_release_parser() -> list[Release]:
    async with (ClientSession() as _session):
        _releases = []
        for _release_type in ("single", "album"):
            async with _session.get(f"https://www.up-front-works.jp/release/search/?g={_release_type}") as _resp:
                release_counts = int(re.search(
                    r"\((\d*?)\)",
                    BeautifulSoup(await _resp.text(), "lxml").select_one("div#sub_navi a.active").get_text()
                ).group(1))
            _detail_pages = []
            for _page_order in range(1, (release_counts // 20) + 2):
                async with _session.get(
                        f"https://www.up-front-works.jp/release/search/?g={_release_type}&p={_page_order}"
                ) as _resp:
                    for _container in BeautifulSoup(await _resp.text(), "lxml").select("div#release_list a.box"):
                        if _container.select_one("img")["src"] == \
                                "https://cdn.helloproject.com/img/release/s/nowprinting.jpg":
                            continue
                        _detail_pages.append(
                            urljoin(_resp.url.__str__(), _container["href"])
                        )
            _page_contents = []
            _sem = asyncio.Semaphore(10)

            async def _fetch_detail_page(_detail_page: str):
                async with _sem:
                    async with _session.get(_detail_page) as __resp:
                        return await __resp.text()

            for _detail_page in _detail_pages:
                _page_contents.append(_fetch_detail_page(_detail_page))
            for _page_content in await asyncio.gather(*_page_contents):
                # noinspection PyTypeChecker
                _releases.append(Release(
                    title="",
                    artist="",
                    release_date=date.fromisocalendar(1970, 1, 1),
                    label="",
                    release_type=_release_type,
                    packages=[]

                ))
                _disc_category = ""
                for block in BeautifulSoup(_page_content, "lxml").select_one("div#right").find_all(recursive=False):
                    match block.name:
                        case "h2":
                            _releases[-1].title = block.contents[0].__str__().strip()
                            _releases[-1].artist = block.find("h3").get_text(strip=True)
                        case "h3":
                            _data = block.get_text(strip=True)
                            if (_disc_type := re.search("【(.*?)】", _data)) is not None:
                                _disc_type = _disc_type.group(1)
                            _pn = re.search("([A-Z]{3,4})-([0-9]{,5})", _data).groups()
                            _pn_alpha = _pn[0]
                            _pn_serial = int(_pn[1])
                            _releases[-1].packages.append(Package(
                                disc_type=_disc_type,
                                discs=[]
                            ))
                        case "h4":
                            _disc_category = block.get_text(strip=True)
                        case "table":
                            match block.attrs.get("class", []):
                                case ["data1"]:
                                    _data = [_row.get_text(strip=True) for _row in block.select("td.columnB")]
                                    _release_date = date.strptime(_data[1], "%Y/%m/%d")
                                    _release_label = _data[2]
                                    _releases[-1].release_date = _release_date
                                    _releases[-1].label = _release_label

                                case ["data2"]:
                                    if not "CD" in _disc_category:
                                        _disc_category = ""
                                        continue
                                    _tracks = []
                                    if "収録内容未定" in block.get_text(strip=True):
                                        pass
                                    else:
                                        for _i, _row in enumerate(list(more_itertools.chunked(block.select("tr")[1:], n=2)), start=1):
                                            row_1 = _row[0].select("td")
                                            row_2 = _row[1].select("td")
                                            _tracks.append(Track(
                                                order=_i,
                                                title=row_1[1].get_text(strip=True),
                                                lyricist=row_1[3].get_text(strip=True),
                                                composer=row_1[4].get_text(strip=True),
                                                arranger=row_1[5].get_text(strip=True),
                                                singer=row_2[0].get_text(strip=True).removeprefix("歌：")
                                            ))
                                    _releases[-1].packages[-1].discs.append(Disc(
                                        disc_number=f"{_pn_alpha}-{_pn_serial}",
                                        tracks=_tracks
                                    ))
                                    _pn_serial += 1

                        case _:
                            print("Unhandled block:", block.name, block.get("class"), file=sys.stderr)
                            pass
        return _releases


if __name__ == '__main__':
    _columns = [
        "Release Title", "Artist", "Release Date", "Label", "Release Type",
        "Disc Type", "Disc Number", "Track Order", "Track Title", "Lyricist", "Composer",
        "Arranger", "Singer"
    ]
    hp_list = []
    for release in asyncio.run(hello_project_release_parser()):
        for package in release.packages:
            for disc in package.discs:
                for track in disc.tracks:
                    hp_list.append(
                        dict(zip(_columns,
                                 [release.title, release.artist, release.release_date, release.label,
                                  release.release_type, package.disc_type, disc.disc_number, track.order, track.title,
                                  track.lyricist, track.composer, track.arranger, track.singer]))
                    )

    ufw_list = []
    for release in asyncio.run(upfront_works_release_parser()):
        for package in release.packages:
            for disc in package.discs:
                for track in disc.tracks:
                    ufw_list.append(
                        dict(zip(_columns,
                                 [release.title, release.artist, release.release_date, release.label,
                                  release.release_type, package.disc_type, disc.disc_number, track.order, track.title,
                                  track.lyricist, track.composer, track.arranger, track.singer]))
                    )

    merged_registry = {}

    for row in hp_list:
        key = (row["Disc Number"], row["Track Title"], row["Track Order"])
        merged_registry[key] = row

    for row in ufw_list:
        key = (row["Disc Number"], row["Track Title"], row["Track Order"])

        if key not in merged_registry:
            merged_registry[key] = row
        else:
            existing_row = merged_registry[key]
            for field, new_val in row.items():
                old_val = existing_row.get(field)

                normalize_val = lambda val: str(val).strip() if val is not None else ""
                norm_old = normalize_val(old_val)
                norm_new = normalize_val(new_val)

                if norm_old != norm_new:
                    if not norm_old and norm_new:
                        existing_row[field] = new_val
                    elif norm_old and norm_new:
                        print(
                            f"[Warning] Conflict for Disc '{key[0]}' - Track '{key[1]}' in field '{field}':\n"
                            f"    HP:  {old_val}\n"
                            f"    UFW: {new_val}",
                            file=sys.stderr
                        )
    df = DataFrame(list(merged_registry.values()), columns=_columns)
    df.sort_values(
        by=["Release Date", "Disc Number", "Track Order"],
        ascending=[False, False, True],
        inplace=True
    )
    df = df.reset_index(drop=True)
    print(df)
    df.to_excel("merged_release_registry.xlsx")
