from scrapers.common import KupiStoreConfig, run_kupi_food_scraper
from pathlib import Path


CONFIG = KupiStoreConfig(
    store="Billa",
    url="https://www.kupi.cz/slevy/ovoce-a-zelenina/billa",
    csv_path=Path(__file__).resolve().parent.parent / "billa.csv",
    store_location="Billa stores in Prague via Kupi.cz",
    loyalty_program="Billa Club",
)


def main() -> None:
    run_kupi_food_scraper(CONFIG)


if __name__ == "__main__":
    main()
