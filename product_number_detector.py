import argparse
import asyncio

from minc_parser import search_with_isrc, generate_cookie_jar
from toc_parser import parse_toc, TOC


async def process_toc(toc: TOC):
    _cookie_jar = await generate_cookie_jar()
    for _toc in toc.tracks:
        if _toc.isrc:
            print(f"Track {_toc.track_number} has ISRC: {_toc.isrc}")
            # Here you can call the search_with_isrc function to get product number
            product_number = await search_with_isrc(_cookie_jar,_toc.isrc)
            print(f"Product number for track {_toc.track_number}: {product_number}")
        else:
            print(f"Track {_toc.track_number} does not have an ISRC.")
    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Detect product number from .toc file.')
    parser.add_argument('--toc', required=True, type=str, help='Path to the .toc file')
    args = parser.parse_args()
    asyncio.run(process_toc(parse_toc(args.toc)))
