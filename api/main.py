"""
api/main.py

FastAPI backend for MOEX ETF Analytics Dashboard.

Endpoints:
    GET /                          - Dashboard UI (HTML)
    GET /api/funds                 - Full fund reference list for autocomplete
    GET /api/top-funds             - Top N funds by real return over a period
    GET /api/performance           - Monthly performance data for selected tickers
    GET /api/calculator            - Investment calculator (nominal vs real return)
"""

import os
import sys
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="MOEX ETF Analytics API")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    """Context manager for database connections."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit(1)
    conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ---------------------------------------------------------------------------
# /api/funds - full fund reference list for search / autocomplete
# ---------------------------------------------------------------------------

@app.get("/api/funds")
def get_funds():
    """Returns all funds from the reference table."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT fund_ticker, fund_name_ru AS fund_name, isin, list_level
            FROM analytics.stg_fund_info
            ORDER BY fund_ticker
        """)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# /api/top-funds - top N funds by real return over last N months
# ---------------------------------------------------------------------------

@app.get("/api/top-funds")
def get_top_funds(
    months: int = Query(12, ge=1, le=60, description="Lookback period in months"),
    limit: int = Query(10, ge=1, le=50, description="Number of funds to return"),
):
    """
    Returns top funds ranked by cumulative real return over the last N months.
    Used to populate the default chart view on page load.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            WITH period_returns AS (
                SELECT
                    fund_ticker,
                    EXP(SUM(LN(1 + nominal_return))) - 1 AS cumulative_nominal,
                    EXP(SUM(LN(1 + real_return)))    - 1 AS cumulative_real,
                    COUNT(*) AS months_count
                FROM analytics.fct_fund_monthly_performance
                WHERE
                    report_month >= DATE_TRUNC('month', NOW()) - (%(months)s || ' months')::INTERVAL
                    AND real_return    IS NOT NULL
                    AND nominal_return IS NOT NULL
                GROUP BY fund_ticker
            )
            SELECT
                p.fund_ticker,
                f.fund_name_ru AS fund_name,
                ROUND(p.cumulative_nominal::NUMERIC, 4) AS cumulative_nominal,
                ROUND(p.cumulative_real::NUMERIC,    4) AS cumulative_real,
                p.months_count
            FROM period_returns p
            LEFT JOIN analytics.stg_fund_info f USING (fund_ticker)
            WHERE p.months_count >= %(months)s * 0.8
            ORDER BY p.cumulative_real DESC
            LIMIT %(limit)s
        """, {"months": months, "limit": limit})
        return cur.fetchall()


# ---------------------------------------------------------------------------
# /api/performance - monthly time series for selected tickers
# ---------------------------------------------------------------------------

@app.get("/api/performance")
def get_performance(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. SBMX,AKGD"),
    from_date: Optional[str] = Query(None, alias="from", description="Start date YYYY-MM-DD"),
    to_date: Optional[str]   = Query(None, alias="to",   description="End date YYYY-MM-DD"),
):
    """
    Returns monthly performance data for the requested tickers.
    Also includes IMOEX and inflation series for chart comparison lines.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers provided")
    if len(ticker_list) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 tickers per request")

    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT
                fund_ticker,
                report_month,
                nominal_return,
                real_return,
                index_return,
                alpha_vs_index,
                key_rate
            FROM analytics.fct_fund_monthly_performance
            WHERE
                fund_ticker = ANY(%(tickers)s)
                AND (%(from_date)s IS NULL OR report_month >= %(from_date)s::DATE)
                AND (%(to_date)s   IS NULL OR report_month <= %(to_date)s::DATE)
                AND nominal_return IS NOT NULL
            ORDER BY fund_ticker, report_month
        """, {
            "tickers":   ticker_list,
            "from_date": from_date,
            "to_date":   to_date,
        })
        fund_rows = cur.fetchall()

        # IMOEX + inflation series deduplicated by month
        # inflation_yoy from CBR is annual YoY % — convert to monthly: (1 + r/100)^(1/12) - 1
        cur.execute("""
            SELECT DISTINCT ON (f.report_month)
                f.report_month,
                f.index_return,
                f.key_rate,
                ROUND((POWER(1 + m.inflation_rate::NUMERIC / 100, 1.0/12) - 1)::NUMERIC, 4) AS monthly_inflation
            FROM analytics.fct_fund_monthly_performance f
            LEFT JOIN analytics.stg_cbr_macro m ON f.report_month = m.report_month
            WHERE
                (%(from_date)s IS NULL OR f.report_month >= %(from_date)s::DATE)
                AND (%(to_date)s IS NULL OR f.report_month <= %(to_date)s::DATE)
                AND f.index_return IS NOT NULL
            ORDER BY f.report_month
        """, {"from_date": from_date, "to_date": to_date})

        index_rows = cur.fetchall()

    return {"funds": fund_rows, "index": index_rows}


# ---------------------------------------------------------------------------
# /api/calculator - investment calculator
# ---------------------------------------------------------------------------

@app.get("/api/calculator")
def calculate(
    ticker:    str   = Query(..., description="Fund ticker, e.g. SBMX"),
    amount:    float = Query(..., gt=0, description="Initial investment in RUB"),
    from_date: str   = Query(..., alias="from", description="Start date YYYY-MM-DD"),
    to_date:   str   = Query(..., alias="to",   description="End date YYYY-MM-DD"),
):
    """
    Calculates investment result for a given fund, amount, and period.

    Cumulative return is computed as the product of monthly returns
    using the log-sum trick: exp(sum(ln(1 + r))) - 1

    Returns nominal and real final values in RUB.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*)                                       AS months_count,
                EXP(SUM(LN(1 + nominal_return))) - 1          AS cumulative_nominal,
                EXP(SUM(LN(NULLIF(1 + real_return, 0)))) - 1  AS cumulative_real
            FROM analytics.fct_fund_monthly_performance
            WHERE
                fund_ticker    = %(ticker)s
                AND report_month >= %(from_date)s::DATE
                AND report_month <= %(to_date)s::DATE
                AND nominal_return IS NOT NULL
        """, {
            "ticker":    ticker.strip().upper(),
            "from_date": from_date,
            "to_date":   to_date,
        })
        row = cur.fetchone()

    if not row or not row["months_count"]:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for {ticker} in the requested period"
        )

    cumulative_nominal = float(row["cumulative_nominal"] or 0)
    cumulative_real    = float(row["cumulative_real"]    or 0)

    return {
        "ticker":              ticker.upper(),
        "amount":              amount,
        "from_date":           from_date,
        "to_date":             to_date,
        "months_count":        int(row["months_count"]),
        "cumulative_nominal":  round(cumulative_nominal, 4),
        "cumulative_real":     round(cumulative_real,    4),
        "nominal_final_value": round(amount * (1 + cumulative_nominal), 2),
        "real_final_value":    round(amount * (1 + cumulative_real),    2),
    }