"""
ingestion/moex.py

Fetches data from MOEX ISS API and writes it to the raw schema in PostgreSQL.

Three public functions:
    fetch_fund_list()                          - fund reference data (TQTF board)
    fetch_fund_prices(secid, from_date, to_date) - daily OHLCV prices for a fund
    fetch_index_prices(from_date, to_date)       - daily IMOEX index values

Each function returns the number of rows written.
No transformations are applied here - that is dbt's responsibility.

Dependencies: requests, psycopg2-binary
Environment variables: DATABASE_URL (postgres://user:pass@host:port/db)
"""

import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

import psycopg2
import psycopg2.extras
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://iss.moex.com/iss"
PAGE_SIZE = 100      # ISS always returns up to 100 rows per page
REQUEST_DELAY = 0.2  # seconds between paginated requests - be polite to the API
MAX_RETRIES = 3
RETRY_DELAY = 5      # seconds before retrying a failed request


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _get(url: str, params: dict) -> dict:
    """
    GET request to ISS with retries. Returns parsed JSON.

    Forces iss.json=extended (response is [meta, data] list)
    and iss.meta=off (meta block is empty, structure stays the same).
    """
    params = {**params, "iss.json": "extended", "iss.meta": "off"}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

def _get_connection():
    """Creates a psycopg2 connection from DATABASE_URL environment variable."""
    database_url = os.environ["DATABASE_URL"]
    return psycopg2.connect(database_url)


# ---------------------------------------------------------------------------
# fetch_fund_list
# ---------------------------------------------------------------------------

def fetch_fund_list() -> int:
    """
    Fetches the reference list of mutual funds and ETFs from the TQTF board.

    Full overwrite on every run - the list is small (~100-150 rows)
    and we always want it to reflect the current state of the board.

    Returns:
        Number of rows written.
    """
    url = f"{BASE_URL}/engines/stock/markets/shares/boards/TQTF/securities.json"
    params = {
        "iss.only": "securities",
        "securities.columns": "SECID,SECNAME,SHORTNAME,ISIN,LISTLEVEL",
    }

    logger.info("Fetching fund list from TQTF board")
    data = _get(url, params)

    # With iss.json=extended ISS returns [meta, data].
    # data[1] is a dict of block_name -> list of row dicts.
    rows = data[1].get("securities", [])

    if not rows:
        logger.warning("fund_list: empty response")
        return 0

    records = [
        (
            row.get("SECID"),
            row.get("SECNAME"),
            row.get("SHORTNAME"),
            row.get("ISIN"),
            str(row.get("LISTLEVEL")) if row.get("LISTLEVEL") is not None else None,
        )
        for row in rows
    ]

    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM raw.fund_info")
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO raw.fund_info (secid, secname, shortname, isin, listlevel)
                VALUES %s
                """,
                records,
            )
        conn.commit()

    logger.info("fund_list: wrote %d rows", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# fetch_fund_prices
# ---------------------------------------------------------------------------

def fetch_fund_prices(
    secid: str,
    from_date: str,
    to_date: Optional[str] = None,
) -> int:
    """
    Fetches daily OHLCV prices for a single fund across all boards.

    Using the global /history/securities/ endpoint instead of specific boards
    because funds frequently switch major trading boards (e.g., TQTF to TQBR).

    Args:
        secid:     Fund ticker, e.g. 'SBSP'.
        from_date: Start of period, 'YYYY-MM-DD'.
        to_date:   End of period, 'YYYY-MM-DD'. Defaults to yesterday.

    Returns:
        Number of rows written.
    """
    if to_date is None:
        to_date = str(date.today() - timedelta(days=1))

    # CHANGED: Using universal history endpoint instead of explicit engine/market/board
    url = f"{BASE_URL}/history/engines/stock/markets/shares/securities/{secid}.json"
    
    params = {
        "from": from_date,
        "till": to_date,
        "history.columns": "SECID,TRADEDATE,OPEN,HIGH,LOW,CLOSE,VOLUME,VALUE",
    }

    logger.info("Fetching prices for %s from %s to %s", secid, from_date, to_date)

    all_records = []
    start = 0

    while True:
        data = _get(url, {**params, "start": start})
        rows = data[1].get("history", [])

        if not rows:
            break

        for row in rows:
            all_records.append((
                row.get("SECID") or secid,
                row.get("TRADEDATE"),
                _to_str(row.get("OPEN")),
                _to_str(row.get("HIGH")),
                _to_str(row.get("LOW")),
                _to_str(row.get("CLOSE")),
                _to_str(row.get("VOLUME")),
                _to_str(row.get("VALUE")),
            ))

        logger.debug("%s: fetched page start=%d, got %d rows", secid, start, len(rows))

        if len(rows) < PAGE_SIZE:
            break

        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

    if not all_records:
        logger.warning("fund_prices: no data for %s %s - %s", secid, from_date, to_date)
        return 0

    _insert_prices("raw.fund_prices", all_records)
    logger.info("fund_prices: wrote %d rows for %s", len(all_records), secid)
    return len(all_records)


# ---------------------------------------------------------------------------
# fetch_index_prices
# ---------------------------------------------------------------------------

def fetch_index_prices(
    from_date: str,
    to_date: Optional[str] = None,
) -> int:
    """
    Fetches daily values of the IMOEX index.

    Indexes live under engine=stock, market=index, board=SNDX.

    Args:
        from_date: Start of period, 'YYYY-MM-DD'.
        to_date:   End of period, 'YYYY-MM-DD'. Defaults to yesterday.

    Returns:
        Number of rows written.
    """
    if to_date is None:
        to_date = str(date.today() - timedelta(days=1))

    url = (
        f"{BASE_URL}/history/engines/stock/markets/index"
        f"/boards/SNDX/securities/IMOEX.json"
    )
    params = {
        "from": from_date,
        "till": to_date,
        "history.columns": "SECID,TRADEDATE,OPEN,HIGH,LOW,CLOSE,VOLUME,VALUE",
    }

    logger.info("Fetching IMOEX from %s to %s", from_date, to_date)

    all_records = []
    start = 0

    while True:
        data = _get(url, {**params, "start": start})
        rows = data[1].get("history", [])

        if not rows:
            break

        for row in rows:
            all_records.append((
                "IMOEX",
                row.get("TRADEDATE"),
                _to_str(row.get("OPEN")),
                _to_str(row.get("HIGH")),
                _to_str(row.get("LOW")),
                _to_str(row.get("CLOSE")),
                _to_str(row.get("VOLUME")),
                _to_str(row.get("VALUE")),
            ))

        if len(rows) < PAGE_SIZE:
            break

        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

    if not all_records:
        logger.warning("index_prices: no data for IMOEX %s - %s", from_date, to_date)
        return 0

    _insert_prices("raw.index_prices", all_records)
    logger.info("index_prices: wrote %d rows for IMOEX", len(all_records))
    return len(all_records)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_str(value) -> Optional[str]:
    """Converts any value to string. None stays None."""
    if value is None:
        return None
    return str(value)


def _insert_prices(table: str, records: list) -> None:
    """Appends price rows to the given table. No deduplication."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                f"""
                INSERT INTO {table}
                    (secid, tradedate, open, high, low, close, volume, value)
                VALUES %s
                """,
                records,
                page_size=500,
            )
        conn.commit()


# ---------------------------------------------------------------------------
# CLI - for local testing and manual backfills
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="MOEX ISS ingestion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("fund-list", help="Refresh fund reference table")

    p_fund = subparsers.add_parser("fund-prices", help="Load prices for a fund")
    p_fund.add_argument("secid", help="Fund ticker, e.g. SBSP")
    p_fund.add_argument("--from-date", default="2018-01-01")
    p_fund.add_argument("--to-date", default=None)

    p_idx = subparsers.add_parser("index-prices", help="Load IMOEX index values")
    p_idx.add_argument("--from-date", default="2018-01-01")
    p_idx.add_argument("--to-date", default=None)

    args = parser.parse_args()

    if args.command == "fund-list":
        n = fetch_fund_list()
        print(f"Done: {n} rows")

    elif args.command == "fund-prices":
        n = fetch_fund_prices(args.secid, args.from_date, args.to_date)
        print(f"Done: {n} rows")

    elif args.command == "index-prices":
        n = fetch_index_prices(args.from_date, args.to_date)
        print(f"Done: {n} rows")