{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_sources', 'fund_info') }}
),

renamed as (
    select
        upper(trim(secid)) as fund_ticker,
        trim(secname) as fund_name_ru,
        trim(shortname) as fund_short_name,
        trim(isin) as isin,
        cast(listlevel as integer) as list_level,
        ingested_at
    from source
),

deduplicated as (
    select *,
        row_number() over (
            partition by fund_ticker 
            order by ingested_at desc
        ) as rn
    from renamed
)

select
    fund_ticker,
    fund_name_ru,
    fund_short_name,
    isin,
    list_level
from deduplicated
where rn = 1