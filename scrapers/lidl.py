from scrapers.common import KupiStoreConfig, run_kupi_scraper
from pathlib import Path


CONFIG = KupiStoreConfig(
    store="Lidl",
    url="https://www.kupi.cz/slevy/ovoce-a-zelenina/lidl",
    csv_path=Path(__file__).resolve().parent.parent / "lidl.csv",
    store_location="Lidl stores in Prague via Kupi.cz",
    loyalty_program="Lidl Plus",
)


def main() -> None:
    run_kupi_scraper(CONFIG)


if __name__ == "__main__":
    main()
