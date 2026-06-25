"""
airflow/dags/moex_analytics.py

Production ELT pipeline orchestrating macroeconomic data ingestion from CBR,
financial market data ingestion from MOEX, and downstream dbt Core transformations.

Pipeline Topology:
    1. Parallel Ingestion:
       - Fetch CBR monthly macro metrics (Inflation, Key Rate)
       - Fetch MOEX daily benchmark index values (IMOEX)
       - Fetch MOEX active mutual funds & ETFs registry list
    2. Sequential Ingestion:
       - Loop through active registry and backfill daily close prices for all funds
    3. Analytical Layer:
       - Run dbt models (Staging views and final Performance Fact tables)
       - Run dbt data quality integrity tests
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

# Default configuration settings applied to all tasks
default_args = {
    'owner': 'airflow',
    'depends_on_past': True,
    'wait_for_downstream': True,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='moex_etf_pipeline',
    default_args=default_args,
    description='End-to-end ELT pipeline for MOEX funds analysis and CBR macro data',
    schedule='@daily',
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=['moex', 'cbr', 'dbt'],
) as dag:

    # -------------------------------------------------------------------------
    # DATA INGESTION LAYER (Sourced via Python CLI modules)
    # -------------------------------------------------------------------------

    # Task 1: Fetch macro indicators (Inflation YoY / Key Rate) from Central Bank SOAP service
    ingest_cbr_macro = BashOperator(
        task_id='ingest_cbr_macro',
        bash_command='python /opt/airflow/ingestion/cbr.py macro --from-date 2018-01-01',
    )

    # Task 2: Fetch historical daily values for the benchmark IMOEX index
    ingest_moex_index_prices = BashOperator(
        task_id='ingest_moex_index_prices',
        bash_command='python /opt/airflow/ingestion/moex.py index-prices --from-date 2018-01-01',
    )

    # Task 3: Refresh the baseline reference registry of allowed mutual funds and ETFs
    ingest_moex_fund_list = BashOperator(
        task_id='ingest_moex_fund_list',
        bash_command='python /opt/airflow/ingestion/moex.py fund-list',
    )

    # Task 4: Extract tickers from database registry and download daily OHLCV for each fund
    ingest_all_fund_prices = BashOperator(
        task_id='ingest_all_fund_prices',
        bash_command='python /opt/airflow/ingestion/fetch_all_prices.py',
    )

    # -------------------------------------------------------------------------
    # DATA TRANSFORMATION LAYER (dbt Core executing inside the container context)
    # -------------------------------------------------------------------------

    # Task 5: Execute dbt transformations (builds staging views and analytics tables)
    run_dbt_transformations = BashOperator(
        task_id='run_dbt_transformations',
        bash_command='''
            cd /opt/airflow/dbt && \
            dbt deps && \
            dbt run --profiles-dir .
        ''',
    )

    # Task 6: Audit data quality via predefined dbt assertions (null constraints, uniqueness)
    run_dbt_tests = BashOperator(
        task_id='run_dbt_tests',
        bash_command='cd /opt/airflow/dbt && dbt test --profiles-dir .',
    )

    # -------------------------------------------------------------------------
    # PIPELINE TASK DEPENDENCY GRAPH
    # -------------------------------------------------------------------------

    # We cannot pull pricing details for funds until we have an updated registry of tickers
    ingest_moex_fund_list >> ingest_all_fund_prices

    # The downstream dbt modeling layer requires all raw ingestion targets to be fully synchronized
    [
        ingest_cbr_macro, 
        ingest_moex_index_prices, 
        ingest_all_fund_prices
    ] >> run_dbt_transformations >> run_dbt_tests