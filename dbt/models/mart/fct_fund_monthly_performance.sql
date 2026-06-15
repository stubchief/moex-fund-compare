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

-- 1. Get the first and last close price for each fund per month using analytic windows
fund_prices_window as (
    select
        fund_ticker,
        date_trunc('month', trade_date)::date as report_month,
        first_value(close_price) over (
            partition by fund_ticker, date_trunc('month', trade_date) 
            order by trade_date 
            rows between unbounded preceding and unbounded following
        ) as price_start,
        last_value(close_price) over (
            partition by fund_ticker, date_trunc('month', trade_date) 
            order by trade_date 
            rows between unbounded preceding and unbounded following
        ) as price_end
    from fund_prices
),

-- Get unique prices per month
fund_monthly_prices as (
    select distinct 
        fund_ticker, 
        report_month, 
        price_start, 
        price_end
    from fund_prices_window
),

-- 2. Aggregate monthly volumes safely using a clean GROUP BY
fund_monthly_volume as (
    select
        fund_ticker,
        date_trunc('month', trade_date)::date as report_month,
        sum(volume_rub) as total_monthly_volume_rub
    from fund_prices
    group by fund_ticker, date_trunc('month', trade_date)
),

-- 3. Calculate MOEX index return for the same months
index_monthly as (
    select distinct
        date_trunc('month', trade_date)::date as report_month,
        first_value(close_price) over (
            partition by date_trunc('month', trade_date) order by trade_date
            rows between unbounded preceding and unbounded following
        ) as index_start,
        last_value(close_price) over (
            partition by date_trunc('month', trade_date) order by trade_date
            rows between unbounded preceding and unbounded following
        ) as index_end
    from index_prices
),

-- 4. Combine all components into a single fact dataset
joined as (
    select
        p.fund_ticker,
        p.report_month,
        
        -- Nominal return calculation
        round(((p.price_end - p.price_start) / p.price_start), 4) as nominal_return,
        
        -- Benchmark index return
        round(((i.index_end - i.index_start) / i.index_start), 4) as index_return,
        
        -- Inflation rate from CBR (converted to decimal format)
        round(power(1 + m.inflation_rate / 100.0, 1.0/12) - 1, 4) as monthly_inflation,
        
        m.key_rate,
        v.total_monthly_volume_rub
    from fund_monthly_prices p
    left join fund_monthly_volume v on p.fund_ticker = v.fund_ticker and p.report_month = v.report_month
    left join index_monthly i on p.report_month = i.report_month
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