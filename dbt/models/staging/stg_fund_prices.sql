{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_sources', 'fund_prices') }}
    where close is not null
),

renamed as (
    select
        upper(trim(secid)) as fund_ticker,
        cast(tradedate as date) as trade_date,
        cast(close as numeric(12, 4)) as close_price,
        cast(value as numeric(16, 2)) as volume_rub,
        ingested_at
    from source
),

deduplicated as (
    select *,
        row_number() over (
            partition by fund_ticker, trade_date 
            order by ingested_at desc
        ) as rn
    from renamed
)

select
    fund_ticker,
    trade_date,
    close_price,
    volume_rub
from deduplicated
where rn = 1