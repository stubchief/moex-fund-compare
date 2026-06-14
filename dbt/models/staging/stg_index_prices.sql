{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_sources', 'index_prices') }}
),

renamed as (
    select
        upper(trim(secid)) as index_ticker,
        cast(tradedate as date) as trade_date,
        cast(close as numeric(12, 4)) as close_price,
        cast(value as numeric(16, 2)) as volume_rub,
        ingested_at
    from source
),

deduplicated as (
    select *,
        row_number() over (
            partition by index_ticker, trade_date 
            order by ingested_at desc
        ) as rn
    from renamed
)

select
    index_ticker,
    trade_date,
    close_price,
    volume_rub
from deduplicated
where rn = 1