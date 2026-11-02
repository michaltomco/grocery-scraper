"""Daily BILLA entry point: fail closed until direct offers are validated.

The former third-party source is intentionally not used. See the checked-in
BILLA source evidence under tests/fixtures/billa/ for the recovery blocker.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BillaConfig:
    store: str = "Billa"
    csv_path: Path = Path(__file__).resolve().parent.parent / "billa.csv"


CONFIG = BillaConfig()


class BillaScrapeError(RuntimeError):
    """No safe BILLA refresh is available; preserve the existing snapshot."""


def main() -> None:
    raise BillaScrapeError(
        "BILLA direct source offer parser is not validated: "
        "https://www.billa.cz/letaky-billa supplies PDFs with mixed validity "
        "periods and graphical loyalty conditions; shop.billa.cz redirects "
        "to a partner-delivery page. Refresh disabled; existing snapshot "
        "and history left untouched. No third-party fallback."
    )


if __name__ == "__main__":
    main()
