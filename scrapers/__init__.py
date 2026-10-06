"""Grocery Scraper package.

Public module layout:
    scrapers.albert   - Albert via Kupi.cz (retained for migration)
    scrapers.billa    - Billa via BILLA.cz PDF (direct retailer source)
    scrapers.lidl     - Lidl directly from lidl.cz (default for daily refresh)
    scrapers.tesco    - Tesco via Kupi.cz (retained unless a publisher replaces it)
"""
from scrapers import albert, billa, lidl, tesco  # noqa: F401

__all__ = ["albert", "billa", "lidl", "tesco"]
