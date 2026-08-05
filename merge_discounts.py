try:
    from scrapers.common import merge_csvs
except ModuleNotFoundError:
    from common import merge_csvs
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INPUT_CSVS = [ROOT / name for name in ("lidl.csv", "tesco.csv", "albert.csv", "billa.csv")]
OUTPUT_CSV = ROOT / "all_discounts.csv"


def main() -> None:
    rows = merge_csvs(INPUT_CSVS, OUTPUT_CSV)
    print(f"Merged {len(rows)} rows into {OUTPUT_CSV.name}")


if __name__ == "__main__":
    main()
