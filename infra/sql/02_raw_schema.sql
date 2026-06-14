-- Switch context to the analytical database
\connect etf_db

-- =============================================================
-- raw schema DDL
--
-- Data is stored exactly as received from the API:
-- all fields are TEXT, no deduplication, no value changes.
-- ingested_at is the only field we add ourselves.
--
-- Applied once manually (or via Docker init / CI deploy).
-- Transformations start in dbt staging, not here.
-- =============================================================

CREATE SCHEMA IF NOT EXISTS raw;

-- -------------------------------------------------------------
-- Fund reference data (mutual funds / ETFs from TQTF board)
-- Fully overwritten on each weekly run.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.fund_info (
    secid           TEXT        NOT NULL,   -- ticker, e.g. SBSP
    secname         TEXT,                   -- full name
    shortname       TEXT,                   -- short name
    isin            TEXT,                   -- ISIN code
    listlevel       TEXT,                   -- listing level (1 / 2 / 3)
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_fund_info_secid
    ON raw.fund_info (secid);

-- -------------------------------------------------------------
-- Historical daily prices for funds (TQTF board)
-- One row = one trading day for one ticker.
-- Duplicates are possible on re-runs; deduplication is in dbt staging.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.fund_prices (
    secid           TEXT        NOT NULL,
    tradedate       TEXT        NOT NULL,   -- 'YYYY-MM-DD', string as returned by API
    open            TEXT,
    high            TEXT,
    low             TEXT,
    close           TEXT,
    volume          TEXT,                   -- number of units traded
    value           TEXT,                   -- traded value in RUB
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_fund_prices_secid_date
    ON raw.fund_prices (secid, tradedate);

-- -------------------------------------------------------------
-- Historical daily values of the IMOEX index
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.index_prices (
    secid           TEXT        NOT NULL,   -- always 'IMOEX'
    tradedate       TEXT        NOT NULL,
    open            TEXT,
    high            TEXT,
    low             TEXT,
    close           TEXT,
    volume          TEXT,
    value           TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_index_prices_secid_date
    ON raw.index_prices (secid, tradedate);

-- -------------------------------------------------------------
-- Monthly Macro Indicators from CBR (Inflation & Key Rate)
-- One row = one calendar month.
-- Data stored as TEXT exactly as received from the SOAP API.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.cbr_macro (
    period          TEXT        NOT NULL,   -- 'YYYY-MM-DD' (normalized from MM.YYYY)
    key_rate        TEXT,                   -- e.g. '16.00'
    inflation_yoy   TEXT,                   -- e.g. '7.44' (Inflation Year-over-Year %)
    target_inflation TEXT,                  -- e.g. '4.00' (CBR target)
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_cbr_macro_period
    ON raw.cbr_macro (period);