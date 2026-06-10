from datetime import datetime
from json import loads

import requests
from bs4 import BeautifulSoup


def main():
    hp_release_page = BeautifulSoup(requests.get("https://helloproject.com/release/").text, "lxml")
    version_dir = loads(str(hp_release_page.find("astro-island").get("props")))["versionDir"][1]
    years = list(map(lambda v: v[1], loads(str(hp_release_page.find("astro-island").get("props")))["years"][1]))
    print(f"Latest release version: {version_dir}")
    print(f"Release years: {years}")
    for year in years:
        if int(year) < 2024:
            # if year != "2026":
            continue
        year_json = requests.get(f"https://helloproject.com/json/{version_dir}/{year}_releases.json").json()
        print(f"\n=== {year} Releases ===")
        for release in year_json["items"]:
            if "single" not in release["category"] and "album" not in release["category"]:
                continue
            print([release["category"], release["title"], release["link"].split("/")[2]])
            hp_detail_page = BeautifulSoup(requests.get("https://helloproject.com" + release["link"]).text, "lxml")
            release_title = hp_detail_page.select_one("h1.ReleaseHead__mainName").text.strip()
            print(f"Release title: {release_title}")
            release_group = hp_detail_page.select_one("div.ReleaseHead__mainTitle > div:nth-of-type(2)").text.strip()
            print(f"Release group: {release_group}")
            release_date_string = hp_detail_page.select_one(
                "div.ReleaseHead__mainDetails > dl:nth-of-type(1) dd").text.strip()
            release_date = datetime.strptime(release_date_string, "%Y.%m.%d")
            print(f"Release date: {release_date.strftime('%Y-%m-%d')}")
            label = hp_detail_page.select_one("div.ReleaseHead__mainDetails > dl:nth-of-type(2) dd").text.strip()
            print(f"Label: {label}")
            for release_edition in hp_detail_page.select("div.ReleaseEdition"):
                print(" " + edition[0].text.strip() if (edition := release_edition.select("div.ReleaseEdition__name")) else "通常盤")
                for disk_num, disk in enumerate(release_edition.select("div.TrackList")):
                    if disk.select_one("div.ReleaseEdition__mediaType").text.strip() != "CD":
                        continue
                    print(" " * 2 + f"Disk {disk_num + 1}:")
                    for track in disk.select("div.TrackListItem"):
                        track_number = int(track.select_one("div.TrackListItem__index").text.strip().removesuffix("."))
                        track_title = track.select_one("div.TrackListItem__title").text.strip()
                        lyricist = None
                        composer = None
                        arranger = None
                        singer = None
                        for note in track.select("div.TrackListItem__notes span"):
                            if note.text.strip().startswith("作詞："):
                                lyricist = note.text.strip()[3:]
                            elif note.text.strip().startswith("作曲："):
                                composer = note.text.strip()[3:]
                            elif note.text.strip().startswith("編曲："):
                                arranger = note.text.strip()[3:]
                            elif note.text.strip().startswith("歌："):
                                singer = note.text.strip()[2:]
                        print(
                            " " * 3 +
                            f"{track_number}. {track_title} (Lyricist: {lyricist}, Composer: {composer}, Arranger: {arranger}, Singer: {singer})"
                        )


if __name__ == '__main__':
    main()
