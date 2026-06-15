"""
ingestion/cbr.py

Fetches monthly macroeconomic data (Inflation YoY and Key Rate)
from the CBR SOAP Web Service and writes it to the raw schema in PostgreSQL.

One public function:
    fetch_cbr_macro(from_date, to_date) - monthly macro indicators

Dependencies: requests, psycopg2-binary
Environment variables: DATABASE_URL
"""

import logging
import os
import time
from datetime import date
from typing import Optional
import xml.etree.ElementTree as ET

import psycopg2
import psycopg2.extras
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SOAP_URL = "https://www.cbr.ru/secinfo/secinfo.asmx"
SOAP_ACTION = "http://web.cbr.ru/InflationXML"

MAX_RETRIES = 3
RETRY_DELAY = 5

SOAP_BODY_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <InflationXML xmlns="http://web.cbr.ru/">
      <DateFrom>{from_date}T00:00:00</DateFrom>
      <DateTo>{to_date}T00:00:00</DateTo>
    </InflationXML>
  </soap:Body>
</soap:Envelope>"""


# ---------------------------------------------------------------------------
# HTTP & DB clients
# ---------------------------------------------------------------------------

def _post_soap(from_date: str, to_date: str) -> str:
    """Sends a POST request to CBR SOAP API with retries. Returns raw XML text."""
    body = SOAP_BODY_TEMPLATE.format(from_date=from_date, to_date=to_date)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": SOAP_ACTION,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                SOAP_URL, data=body.encode("utf-8"), headers=headers, timeout=30
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.RequestException as e:
            logger.warning("Attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


def _get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _normalize_period(dts_str: str) -> str:
    """Converts 'MM.YYYY' (e.g. '01.2024') to ISO date 'YYYY-MM-01'."""
    month, year = dts_str.split(".")
    return f"{year}-{month}-01"


# ---------------------------------------------------------------------------
# Core ingestion function
# ---------------------------------------------------------------------------

def fetch_cbr_macro(
    from_date: str = "2018-01-01",
    to_date: Optional[str] = None,
) -> int:
    """
    Fetches monthly Key Rate, Inflation (YoY), and Target Inflation from CBR.

    infVal is year-over-year annual inflation (%), e.g. 7.44 = +7.44% vs same
    month last year. This is the standard Rosstat CPI series as published by CBR.

    No pagination - CBR returns the full date range in one response.
    Duplicates from re-runs are handled in dbt staging.

    Args:
        from_date: Start date 'YYYY-MM-DD'. Defaults to '2018-01-01'.
        to_date:   End date 'YYYY-MM-DD'. Defaults to today.

    Returns:
        Number of rows written.
    """
    if to_date is None:
        to_date = str(date.today())

    logger.info("Fetching CBR macro indicators from %s to %s", from_date, to_date)
    raw_xml = _post_soap(from_date, to_date)

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as e:
        logger.error("Failed to parse SOAP XML response: %s", e)
        raise

    namespaces = {
        "soap": "http://schemas.xmlsoap.org/soap/envelope/",
        "cbr":  "http://web.cbr.ru/",
    }

    result_node = root.find(".//cbr:InflationXMLResult", namespaces)
    if result_node is None:
        logger.warning("InflationXMLResult node not found in response")
        return 0

    records = []
    for ri in result_node.findall(".//RI"):
        dts      = ri.find("DTS")
        key_rate = ri.find("KeyRate")
        inf_val  = ri.find("infVal")
        aim_val  = ri.find("AimVal")

        if dts is None or dts.text is None:
            continue

        records.append((
            _normalize_period(dts.text.strip()),
            key_rate.text.strip() if key_rate is not None and key_rate.text else None,
            inf_val.text.strip()  if inf_val  is not None and inf_val.text  else None,
            aim_val.text.strip()  if aim_val  is not None and aim_val.text  else None,
        ))

    if not records:
        logger.warning("cbr_macro: no records extracted from response")
        return 0

    with _get_connection() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO raw.cbr_macro (period, key_rate, inflation_yoy, target_inflation)
                VALUES %s
                """,
                records,
            )
        conn.commit()

    logger.info("cbr_macro: wrote %d rows", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# CLI - for local testing and manual backfills
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="CBR SOAP ingestion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("macro", help="Load monthly macro indicators (Inflation & Key Rate)")
    p.add_argument("--from-date", default="2018-01-01")
    p.add_argument("--to-date", default=None)

    args = parser.parse_args()

    if args.command == "macro":
        n = fetch_cbr_macro(args.from_date, args.to_date)
        print(f"Done: {n} rows")