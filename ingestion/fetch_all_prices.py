import os
import sys
import logging
import psycopg2
from datetime import datetime, timedelta
from moex import fetch_fund_prices

logger = logging.getLogger(__name__)

def fetch_prices_for_all_registered_funds(fallback_start_date: str = "2018-01-01") -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable is missing.")
        sys.exit(1)

    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
    except psycopg2.Error as e:
        logger.error("Failed to connect to the database: %s", e)
        sys.exit(1)
    
    try:
        cur.execute("SELECT DISTINCT secid FROM raw.fund_info WHERE secid IS NOT NULL;")
        tickers = [row[0] for row in cur.fetchall()]
    except psycopg2.Error as e:
        logger.error("Failed to query raw.fund_info: %s", e)
        cur.close()
        conn.close()
        sys.exit(1)

    total_funds = len(tickers)
    if total_funds == 0:
        logger.warning("No tickers found in raw.fund_info. Registry might be empty.")
        cur.close()
        conn.close()
        return
        
    logger.info("Found %d funds in registry. Starting sync...", total_funds)
    success_count = 0
    
    for index, ticker in enumerate(tickers, start=1):
        clean_ticker = str(ticker).strip().upper()
        
        try:
            cur.execute(
                "SELECT MAX(tradedate) FROM raw.fund_prices WHERE UPPER(secid) = %s;", 
                (clean_ticker,)
            )
            max_date_row = cur.fetchone()
            
            if max_date_row and max_date_row[0]:
                last_db_date = max_date_row[0]
                
                if isinstance(last_db_date, str):
                    last_db_date = datetime.strptime(last_db_date.split()[0], "%Y-%m-%d").date()
                
                target_start_date = (last_db_date + timedelta(days=1)).strftime("%Y-%m-%d")
                mode_desc = f"INCREMENTAL from {target_start_date}"
            else:
                target_start_date = fallback_start_date
                mode_desc = f"FULL BACKFILL from {target_start_date}"
                
        except psycopg2.Error as e:
            logger.error("Database error tracking checkpoint for %s: %s. Skipping.", clean_ticker, e)
            continue

        today_str = datetime.today().strftime("%Y-%m-%d")
        if target_start_date > today_str:
            logger.info("[%d/%d] Asset %s is up to date.", index, total_funds, clean_ticker)
            success_count += 1
            continue

        logger.info("[%d/%d] Processing %s in %s mode", index, total_funds, clean_ticker, mode_desc)
        
        try:
            rows_written = fetch_fund_prices(clean_ticker, from_date=target_start_date)
            logger.info("Processed %s. Loaded %d rows.", clean_ticker, rows_written)
            success_count += 1
        except Exception as e:
            logger.error("Failed to fetch data for %s: %s", clean_ticker, e)
            
    cur.close()
    conn.close()
    logger.info("Pipeline sync cycle completed. Updated %d/%d targets.", success_count, total_funds)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    fetch_prices_for_all_registered_funds(fallback_start_date="2018-01-01")