try:
    from common import KupiStoreConfig, run_kupi_scraper
except ModuleNotFoundError:
    from scrapers.common import KupiStoreConfig, run_kupi_scraper


CONFIG = KupiStoreConfig(
    store="Albert",
    url="https://www.kupi.cz/slevy/ovoce-a-zelenina/albert",
    csv_path="albert.csv",
    store_location="Albert stores in Prague via Kupi.cz",
    loyalty_program="Můj Albert",
)


def main() -> None:
    run_kupi_scraper(CONFIG)


if __name__ == "__main__":
    main()
