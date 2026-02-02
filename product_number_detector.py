import argparse

from toc_parser import parse_toc

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Detect product number from .toc file.')
    parser.add_argument('--toc', required=True, type=str, help='Path to the .toc file')
    args = parser.parse_args()
    toc = parse_toc(args.toc)
    print(toc)
