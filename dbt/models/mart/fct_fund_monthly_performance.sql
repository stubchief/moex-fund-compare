{{ config(materialized='table') }}

with fund_prices as (
    select * from {{ ref('stg_fund_prices') }}
),

index_prices as (
    select * from {{ ref('stg_index_prices') }}
),

macro_data as (
    select * from {{ ref('stg_cbr_macro') }}
),

-- 1. One row per fund per month: the close price of the LAST trading day of that month.
fund_month_end as (
    select distinct
        fund_ticker,
        date_trunc('month', trade_date)::date as report_month,
        last_value(close_price) over (
            partition by fund_ticker, date_trunc('month', trade_date)
            order by trade_date
            rows between unbounded preceding and unbounded following
        ) as month_end_price
    from fund_prices
),

-- 2. Chain consecutive months: this month's baseline is the PREVIOUS month's
--    closing price, not this month's own first trade. This is what makes
--    monthly returns compound correctly into the true cumulative return -
--    without it, consecutive months don't share a common price point and
--    the product of returns can drift arbitrarily far from reality (this
--    was the source of the runaway cumulative % on the dashboard chart).
fund_monthly_return as (
    select
        fund_ticker,
        report_month,
        month_end_price,
        lag(month_end_price) over (
            partition by fund_ticker order by report_month
        ) as prev_month_end_price
    from fund_month_end
),

-- 3. Monthly traded volume - unaffected by the fix, still a plain sum within the month.
fund_monthly_volume as (
    select
        fund_ticker,
        date_trunc('month', trade_date)::date as report_month,
        sum(volume_rub) as total_monthly_volume_rub
    from fund_prices
    group by fund_ticker, date_trunc('month', trade_date)
),

-- 4. Same chaining fix applied to the IMOEX benchmark.
index_month_end as (
    select distinct
        date_trunc('month', trade_date)::date as report_month,
        last_value(close_price) over (
            partition by date_trunc('month', trade_date) order by trade_date
            rows between unbounded preceding and unbounded following
        ) as month_end_index
    from index_prices
),

index_monthly_return as (
    select
        report_month,
        month_end_index,
        lag(month_end_index) over (order by report_month) as prev_month_end_index
    from index_month_end
),

-- 5. Combine all components into a single fact dataset.
joined as (
    select
        p.fund_ticker,
        p.report_month,

        -- Nominal return: this month-end vs PREVIOUS month-end (chained, telescopes correctly)
        round((p.month_end_price - p.prev_month_end_price) / p.prev_month_end_price, 4) as nominal_return,

        -- Benchmark index return, same chained logic
        round((i.month_end_index - i.prev_month_end_index) / i.prev_month_end_index, 4) as index_return,

        -- Inflation rate from CBR (converted to decimal monthly rate)
        round(power(1 + m.inflation_rate / 100.0, 1.0/12) - 1, 4) as monthly_inflation,

        m.key_rate,
        v.total_monthly_volume_rub
    from fund_monthly_return p
    left join fund_monthly_volume v on p.fund_ticker = v.fund_ticker and p.report_month = v.report_month
    left join index_monthly_return i on p.report_month = i.report_month
    left join macro_data m on p.report_month = m.report_month
)

select
    fund_ticker,
    report_month,
    nominal_return,
    index_return,
    -- Real return adjusted for inflation using Fisher equation: (1 + N) / (1 + I) - 1
    round(((1 + nominal_return) / (1 + monthly_inflation)) - 1, 4) as real_return,
    -- Fund's alpha performance against the benchmark index
    round(nominal_return - index_return, 4) as alpha_vs_index,
    key_rate,
    total_monthly_volume_rub
from joined