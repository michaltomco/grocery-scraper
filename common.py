"""Compatibility shim.

The real implementation lives in ``scrapers/common.py``. Both
``from common import ...`` (when run with the repo root on ``sys.path``) and
``from scrapers.common import ...`` must resolve to the *same* module so that
``ROOT``/``HISTORY_CSV`` are computed identically. Re-exporting here keeps a
single source of truth and prevents a stray copy from pointing ``ROOT`` at the
wrong directory.
"""

from scrapers.common import *  # noqa: F401,F403
from scrapers.common import (  # noqa: F401
    HISTORY_CSV,
    KUPI_BASE_URL,
    KUPI_HEADERS,
    KupiStoreConfig,
    LOCAL_TIMEZONE,
    ROOT,
    append_history,
    canonical_product_name,
    fetch_kupi_products,
    history_key,
    merge_csvs,
    read_csv,
    run_kupi_scraper,
    write_csv,
)
