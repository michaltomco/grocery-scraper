try:
    from common import KupiStoreConfig, run_kupi_scraper
except ModuleNotFoundError:
    from scrapers.common import KupiStoreConfig, run_kupi_scraper


CONFIG = KupiStoreConfig(
    store="Tesco",
    url="https://www.kupi.cz/slevy/ovoce-a-zelenina/tesco",
    csv_path="tesco.csv",
    store_location="Tesco stores in Prague via Kupi.cz",
    loyalty_program="Clubcard",
)


def main() -> None:
    run_kupi_scraper(CONFIG)


if __name__ == "__main__":
    main()
