from scrapers import albert, billa, lidl, tesco
from merge_discounts import main as merge


def main() -> None:
    for scraper in (albert, billa, lidl, tesco):
        scraper.main()
    merge()


if __name__ == "__main__":
    main()
