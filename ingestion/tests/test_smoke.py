"""
Smoke tests for ingestion scripts.
Simple checks that APIs are reachable and return expected data structure.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch
import pytest

from ingestion.moex import fetch_fund_list, fetch_fund_prices
from ingestion.cbr import fetch_cbr_macro


@pytest.fixture(autouse=True)
def mock_db_connection():
    """Bypass database interactions for smoke testing APIs."""
    with patch("ingestion.moex._get_connection") as mock_moex_conn, \
         patch("ingestion.cbr._get_connection") as mock_cbr_conn, \
         patch("psycopg2.extras.execute_values"):
        
        mock_conn = MagicMock()
        mock_moex_conn.return_value = mock_conn
        mock_cbr_conn.return_value = mock_conn
        
        yield mock_conn


def test_fetch_fund_list():
    """Check that we can fetch and parse the list of funds from MOEX."""
    rows = fetch_fund_list()
    assert isinstance(rows, int)
    assert rows > 0, "Expected to fetch at least some funds from MOEX"


def test_fetch_fund_prices():
    """Check that we can fetch and parse historical prices for a fund."""
    from_date = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    rows = fetch_fund_prices("SBMX", from_date=from_date)
    assert isinstance(rows, int)
    assert rows > 0, "Expected to fetch price rows for SBMX"


def test_fetch_cbr_macro():
    """Check that we can fetch and parse macro data from CBR."""
    from_date = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")
    rows = fetch_cbr_macro(from_date=from_date)
    assert isinstance(rows, int)
    assert rows > 0, "Expected to fetch macro records from CBR"