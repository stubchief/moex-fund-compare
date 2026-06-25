"""
ingestion/moex.py

Fetches data from MOEX ISS API and writes it to the raw schema in PostgreSQL.

Three public functions:
    fetch_fund_list()                          - fund reference data (Shares market)
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
# HTTP client & Helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict) -> dict:
    """
    GET request to ISS with retries. Returns parsed JSON.

    Forces iss.json=extended (response is [meta, data] list)
    and iss.meta=off (meta block is empty, structure stays the same).
    """
    params = {**params, "iss.json": "extended", "iss.meta": "off"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


def _get_block(data, block_name: str) -> list:
    """
    Safely extracts the specific data block from MOEX extended JSON array.
    """
    if isinstance(data, list):
        for block in data:
            if isinstance(block, dict) and block_name in block:
                return block[block_name]
    elif isinstance(data, dict) and block_name in data:
        return data[block_name]
    return []


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
    Fetches the reference list of mutual funds and ETFs from the entire shares market.
    """
    # Переключаемся на глобальный эндпоинт рынка акций, так как доска TQTF упразднена
    url = f"{BASE_URL}/engines/stock/markets/shares/securities.json"
    params = {
        "iss.only": "securities",
        "securities.columns": "SECID,SECNAME,SHORTNAME,ISIN,LISTLEVEL",
    }

    logger.info("Fetching fund list from MOEX shares market")
    data = _get(url, params)

    raw_rows = _get_block(data, "securities")
    if not raw_rows:
        logger.warning("fund_list: empty response")
        return 0

    # Нормализуем ключи к нижнему регистру
    rows = [{k.lower(): v for k, v in r.items()} for r in raw_rows]

    # Маркеры для точечного отбора фондов (ETF, БПИФ, ЗПИФ, Паи) из общей массы акций
    keywords = ["etf", "бпиф", "ипиф", "зпиф", "фонд", "пай"]

    records = []
    for row in rows:
        if not row.get("secid"):
            continue

        # Проверяем, что бумага является фондом
        secname = str(row.get("secname", "")).lower()
        shortname = str(row.get("shortname", "")).lower()
        if not any(k in secname or k in shortname for k in keywords):
            continue

        records.append((
            row.get("secid"),
            row.get("secname"),
            row.get("shortname"),
            row.get("isin"),
            str(row.get("listlevel")) if row.get("listlevel") is not None else None,
        ))

    if not records:
        logger.warning("fund_list: no valid records after filtering")
        return 0

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
    """
    if to_date is None:
        to_date = str(date.today() - timedelta(days=1))

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
        raw_rows = _get_block(data, "history")

        if not raw_rows:
            break

        # Нормализуем ключи к нижнему регистру
        rows = [{k.lower(): v for k, v in r.items()} for r in raw_rows]

        for row in rows:
            if not row.get("tradedate"):
                continue
            all_records.append((
                row.get("secid") or secid,
                row.get("tradedate"),
                _to_str(row.get("open")),
                _to_str(row.get("high")),
                _to_str(row.get("low")),
                _to_str(row.get("close")),
                _to_str(row.get("volume")),
                _to_str(row.get("value")),
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
        raw_rows = _get_block(data, "history")

        if not raw_rows:
            break

        # Нормализуем ключи к нижнему регистру
        rows = [{k.lower(): v for k, v in r.items()} for r in raw_rows]

        for row in rows:
            if not row.get("tradedate"):
                continue
            all_records.append((
                "IMOEX",
                row.get("tradedate"),
                _to_str(row.get("open")),
                _to_str(row.get("high")),
                _to_str(row.get("low")),
                _to_str(row.get("close")),
                _to_str(row.get("volume")),
                _to_str(row.get("value")),
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
# CLI
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