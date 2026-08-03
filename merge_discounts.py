try:
    from scrapers.common import merge_csvs
except ModuleNotFoundError:
    from common import merge_csvs


INPUT_CSVS = ["lidl.csv", "tesco.csv", "albert.csv"]
OUTPUT_CSV = "all_discounts.csv"


def main() -> None:
    rows = merge_csvs(INPUT_CSVS, OUTPUT_CSV)
    print(f"Merged {len(rows)} rows into {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
