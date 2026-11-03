"""Conservative recovery of five audited cards per 44sm/45sm cover, NOT a feed.

Supported publications: 44sm_akcni_letak and 45sm_akcni_letak, page 1 ONLY.
Explicit reviewed card/name/price rectangles bound recovery; unreviewed cards,
nonfood, interiors and ambiguous package ranges/multipacks are not emitted.
Geometry is in PDF points, independent of text order/merged text blocks.
Dates come from the explicit cover range, not issue metadata or holiday dates.
The reviewed no-app footer is mandatory; conflicting local conditions are
skipped, unassigned date constraints reject the cover. Member-only offers are
not supported, rather than being relabelled as unconditional. Deposits and
reviewed daily pack limits survive in discount_label. Images and old_price are
intentionally blank; 30-day reference prices are labelled, never sale anchors.

This intentionally bounded recovery requires review/new fixtures for each new
publication/layout. Transport and branch applicability remain caller concerns.
"""
import hashlib
import re
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from scrapers.common import (
    FIELDNAMES, canonical_product_name, clean_text, normalize_unit_price,
    today_timestamp,
)

# Reviewed card rectangles, with separate name/body and featured-price areas.
# This is deliberately not a generic nearest-price parser.
_TEMPLATES = {
    '44sm_akcni_letak': (
        ((55, 200, 215, 350), (65, 220, 130, 280), (140, 240, 205, 305), 'Svijanský Máz', 'Alkohol'),
        ((235, 200, 470, 355), (330, 205, 465, 240), (240, 245, 310, 302), 'Česká chuť Vejce z podestýlky M', 'Mléčné výrobky a vejce'),
        ((10, 395, 160, 500), (15, 398, 100, 440), (100, 435, 150, 480), 'Vepřová plec bez kosti – v celku', 'Maso, uzeniny a ryby'),
        ((165, 395, 312, 500), (165, 410, 245, 455), (250, 435, 305, 480), 'Kuřecí prsa s kostí', 'Maso, uzeniny a ryby'),
        ((315, 385, 470, 500), (315, 415, 385, 470), (400, 400, 460, 445), 'Česká chuť Máslo 82%', 'Mléčné výrobky a vejce'),
    ),
    '45sm_akcni_letak': (
        ((15, 200, 225, 365), (20, 205, 130, 250), (135, 245, 205, 305), 'Vepřová pečeně bez kosti – v celku', 'Maso, uzeniny a ryby'),
        ((10, 385, 160, 500), (15, 385, 90, 420), (100, 435, 155, 480), 'Kuřecí prsní řízky', 'Maso, uzeniny a ryby'),
        ((10, 505, 160, 610), (100, 565, 155, 608), (100, 530, 150, 575), 'Mandarinky volné', 'Ovoce a zelenina'),
        ((320, 310, 470, 500), (400, 310, 470, 368), (400, 385, 460, 435), 'Albert Vejce z podestýlky M', 'Mléčné výrobky a vejce'),
        ((165, 610, 317, 703), (195, 665, 317, 703), (250, 630, 305, 675), 'Velkopopovický Kozel 10', 'Alkohol'),
    ),
}
_PRICE_FONT = 'AlbertDraft22Crossed-Reg'


def _read_cover(pdf_bytes: bytes) -> dict:
    import pymupdf
    try:
        with pymupdf.open(stream=pdf_bytes, filetype='pdf') as document:
            page = document[0]
            spans = [{key: span[key] for key in ('text', 'font', 'size', 'bbox')}
                     for block in page.get_text('dict')['blocks']
                     for line in block.get('lines', []) for span in line['spans']]
            return dict(width=page.rect.width, height=page.rect.height, spans=spans)
    except Exception as exc:
        raise ValueError('Albert PDF cannot be decoded') from exc


def _inside(span, box):
    x0, y0, x1, y1 = span['bbox']
    return box[0] <= x0 <= x1 <= box[2] and box[1] <= y0 <= y1 <= box[3]


def extract_pdf_products(pdf_bytes: bytes, publication_slug: str, source_url: str,
                         scraped_at: str | None = None) -> list[dict]:
    """Return a bounded verified cover subset; raise ValueError when unsupported.

    Transport, branch applicability and publication discovery belong to caller.
    No completeness claim: interior pages are NEVER parsed by this template.
    """
    source = urlsplit(source_url)
    if source.scheme != 'https' or source.netloc not in ('view.publitas.com', 'letaky.albert.cz'):
        raise ValueError('Albert PDF source must be an official HTTPS URL')
    if source.netloc == 'view.publitas.com' and re.fullmatch(r'/90263/\d+/pdfs/[^/]+\.pdf', source.path):
        page_url = urlunsplit(source._replace(fragment='page=1'))
    else:
        base = f'/albert/{publication_slug}' if source.netloc == 'view.publitas.com' else f'/{publication_slug}'
        if not re.fullmatch(re.escape(base) + r'/?(?:page/\d+/?)?', source.path):
            raise ValueError('Albert PDF source does not identify this publication')
        page_url = urlunsplit(source._replace(path=base + '/page/1', query='', fragment=''))
    cover = _read_cover(pdf_bytes)
    if abs(cover['width'] - 481.89) > .1 or abs(cover['height'] - 840.0475) > .1:
        raise ValueError('Unsupported Albert cover layout dimensions')
    spans = cover['spans']
    footer = clean_text(' '.join(s['text'] for s in spans if s['bbox'][1] > 800))
    if 'dostupná všem zákazníkům i bez aplikace Můj Albert.' not in footer:
        raise ValueError('Albert cover loyalty eligibility is unknown')
    header = ' '.join(clean_text(s['text']) for s in spans if s['bbox'][1] < 30)
    matches = re.findall(r'Od (?:čtvrtka )?(\d+)\.\s*(\d+)\.\s*do (\d+)\.\s*(\d+)\.\s*(\d{4})', header)
    if len(matches) != 1:
        raise ValueError('Albert cover has no unambiguous explicit validity range')
    day, month, end_day, end_month, year = map(int, matches[0])
    start, end = date(year, month, day), date(year, end_month, end_day)
    if end < start:
        raise ValueError('Albert cover validity range is reversed')
    template = _TEMPLATES.get(publication_slug, ())
    for span in spans:
        # Ignore the explicit cover range, printing/legal footnote, and the
        # separately printed holiday closure. Any unassigned offer qualifier
        # can affect multiple cards, so this page is not safe to interpret.
        if not 65 < span['bbox'][1] < 800:
            continue
        if any(_inside(span, card) for card, *_ in template):
            continue
        if re.search(r'\bpouze\b|\bplatí\b|\bpři nákupu\b|\b(?:od|do)\s+\d{1,2}\.\s*\d{1,2}\.|víkend', clean_text(span['text']).lower()):
            raise ValueError('Unassigned Albert page validity/offer constraint')
    rows = []
    for card, caption, price_box, expected_name, category in template:
        local = [s for s in spans if _inside(s, card)]
        local_text = clean_text(' '.join(s['text'] for s in local)).lower()
        if re.search(r'\bpouze\b|\bplatí\b|\bpři\b|\bs aplikací\b|\bs kartou\b|\d{1,2}\.\s*\d{1,2}\.', local_text):
            # A conflicting app condition, subperiod or multibuy cannot inherit
            # the cover's ordinary eligibility/dates. Skip, never guess.
            continue
        label = sorted((s for s in local if _inside(s, caption)), key=lambda s: (s['bbox'][1], s['bbox'][0]))
        name = clean_text(' '.join(s['text'] for s in label if s['font'] == 'AlbertText-BoldCondensed' and s['size'] < 12))
        if name != expected_name:
            continue
        prices = []
        for whole in local:
            if not _inside(whole, price_box):
                continue
            if whole['font'] != _PRICE_FONT or whole['size'] < 30 or not re.fullmatch(r'\d+', whole['text']):
                continue
            cents = [s for s in local if s['font'] == _PRICE_FONT and re.fullmatch(r'\d{2}', s['text'])
                     and .56 <= s['size'] / whole['size'] <= .61
                     and abs(s['bbox'][0] - whole['bbox'][2]) < 2
                     and abs(s['bbox'][1] - whole['bbox'][1]) < 3]
            if len(cents) == 1:
                prices.append(int(whole['text']) + int(cents[0]['text']) / 100)
        if len(prices) != 1:
            continue
        price = prices[0]
        if price <= 0:
            continue
        references = [s for s in label if s['font'] == 'AlbertText-RegularConden'
                      and re.fullmatch(r'\d+(?:[.,]\d+)? Kč', clean_text(s['text']))
                      and any(marker['font'] == 'Sipka30dniAlbert-Regular'
                              and abs(marker['bbox'][2] - s['bbox'][0]) < 1
                              and abs(marker['bbox'][1] - s['bbox'][1]) < 3
                              for marker in label)]
        body = clean_text(' '.join(s['text'] for s in label
                                  if s['font'] == 'AlbertText-RegularConden' and s not in references))
        reference_labels = ['Nejnižší cena za 30 dní: ' + clean_text(s['text']) for s in references]
        quantities = [clean_text(part) for part in body.split('•')
                      if re.fullmatch(r'\s*\d+(?:[.,]\d+)?\s*(?:kg|g|ml|l|ks)\s*', part)]
        if len(quantities) != 1:
            continue
        volume = quantities[0]
        if float(re.match(r'[\d.,]+', volume).group().replace(',', '.')) <= 0:
            continue
        limits = [clean_text(s['text']) + ' / den' for s in local
                  if s['font'] == 'AlbertText-BoldCondensed'
                  and re.fullmatch(r'\d+\s*(?:bal\.|ks)', clean_text(s['text']))]
        unit_price = f'{price:g} Kč / {volume}'
        stated = re.findall(r'(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l|ks)\s*=\s*(\d+(?:[.,]\d+)?)\s*Kč', body)
        if len(stated) > 1:
            continue
        if stated:
            amount, unit, stated_price = stated[0]
            amount, stated_price = float(amount.replace(',', '.')), float(stated_price.replace(',', '.'))
            if amount <= 0 or stated_price <= 0:
                continue
            unit_price = f'{stated_price:g} Kč / {amount:g} {unit}'
        row: dict = dict.fromkeys(FIELDNAMES, '')
        row.update(store='Albert', category=category,
                   product_id=f'albert-pdf-{publication_slug}-p1-' + hashlib.sha256(name.encode()).hexdigest()[:16],
                   product_name=f'{name} {volume}', canonical_product_name=canonical_product_name(name),
                   unit_price=unit_price, **normalize_unit_price(unit_price),
                   discount_label=' | '.join(filter(None, [body, *reference_labels, *limits])),
                   price=price, currency='Kč', loyalty_required=False,
                   date_range=f'{start.isoformat()} - {end.isoformat()}',
                   url=page_url, scraped_at=scraped_at or today_timestamp())
        rows.append(row)
    if not rows:
        raise ValueError('No verified Albert cover offers; snapshot must be preserved')
    return rows
