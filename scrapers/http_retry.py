"""Bounded retries for retailer GETs and explicitly read-only GraphQL POSTs."""
from collections.abc import Callable
from email.utils import parsedate_to_datetime
import sys
import time

import requests


def request_with_retry(send: Callable[[], requests.Response], *, label: str) -> requests.Response:
    """At most four attempts, 30s per delay and 60s total retry sleep.

    Do not shorten a server's Retry-After to fit the budget: fail visibly instead.
    JSON/schema validation belongs to callers, outside the retry loop.
    """
    slept = 0.0
    for attempt in range(1, 5):
        try:
            response = send()
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as error:
            if isinstance(error, requests.HTTPError):
                if error.response is None or error.response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise
                reason = f'HTTP {error.response.status_code}'
            else:
                reason = type(error).__name__
            if attempt == 4:
                print(f'{label}: retries exhausted at attempt {attempt}/4 ({reason})', file=sys.stderr, flush=True)
                raise
            delay = 2 ** attempt
            if isinstance(error, requests.HTTPError) and error.response is not None:
                header = error.response.headers.get('Retry-After', '').strip()
                if header.isascii() and header.isdigit():
                    delay = max(delay, int(header))
                elif header:
                    try:
                        deadline = parsedate_to_datetime(header)
                        if deadline.tzinfo is not None:
                            delay = max(delay, deadline.timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            if delay > 30 or slept + delay > 60:
                print(f'{label}: retry delay budget exhausted at attempt {attempt}/4 ({reason}); required {delay:g}s, slept {slept:g}s; snapshot preserved', file=sys.stderr, flush=True)
                raise
            print(f'{label}: attempt {attempt}/4 failed ({reason}); retry in {delay:g}s', file=sys.stderr, flush=True)
            time.sleep(delay)
            slept += delay
    raise AssertionError('Retry loop ended without returning or raising')
