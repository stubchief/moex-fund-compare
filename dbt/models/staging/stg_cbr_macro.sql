{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_sources', 'cbr_macro') }}
),

renamed as (
    select
        cast(period as date) as report_month,
        cast(inflation_yoy as numeric(5, 2)) as inflation_rate,
        cast(key_rate as numeric(5, 2)) as key_rate,
        cast(target_inflation as numeric(5, 2)) as target_inflation,
        ingested_at
    from source
),

deduplicated as (
    select *,
        row_number() over (
            partition by report_month 
            order by ingested_at desc
        ) as rn
    from renamed
)

select
    report_month,
    inflation_rate,
    key_rate,
    target_inflation
from deduplicated
where rn = 1