from scrapers.common import KupiStoreConfig, run_kupi_scraper
from pathlib import Path


CONFIG = KupiStoreConfig(
    store="Albert",
    url="https://www.kupi.cz/slevy/ovoce-a-zelenina/albert",
    csv_path=Path(__file__).resolve().parent.parent / "albert.csv",
    store_location="Albert stores in Prague via Kupi.cz",
    loyalty_program="Můj Albert",
)


def main() -> None:
    run_kupi_scraper(CONFIG)


if __name__ == "__main__":
    main()
