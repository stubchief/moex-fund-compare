# MOEX ETF Analytics

Portfolio project: dashboard and calculator for comparing Russian mutual funds (БПИФы) against inflation and the MOEX index, with a full ELT pipeline behind it.

![Dashboard](docs/dashboard.png)

Pick a fund, a period, and an amount — get nominal return, real return adjusted for CPI, and how it stacks up against IMOEX.

## Architecture

```
MOEX ISS API + CBR SOAP API
        │
        ▼
Airflow (daily)
  ├── ingestion/moex.py  → raw.fund_info, raw.fund_prices, raw.index_prices
  └── ingestion/cbr.py   → raw.cbr_macro
        │
        ▼
dbt
  ├── staging  → casting, dedup
  └── mart     → fct_fund_monthly_performance
        │
        ▼
PostgreSQL (etf_db)
  ├── FastAPI + Jinja2 → dashboard on :8000
  └── Airflow UI       → :8080

Docker Compose — all services
Terraform      — VM provisioning (Yandex Cloud)
```

![Airflow DAG](docs/dag.png)

**Schemas:** `raw` holds data as received from the APIs (everything TEXT, no dedup). `analytics` staging casts types and dedupes. `analytics` mart has one table, `fct_fund_monthly_performance` — monthly nominal/real return, alpha vs IMOEX.

## Stack

Python ingestion · Airflow 2.10 · PostgreSQL 15 · dbt Core 1.8 · FastAPI · vanilla JS + Chart.js + Tailwind · Docker Compose · Terraform

## Data sources

- [MOEX ISS API](https://iss.moex.com/iss/reference/) — fund prices (TQTF board), IMOEX, fund metadata. Free, no auth.
- [CBR SOAP API](https://www.cbr.ru/secinfo/secinfo.asmx) — monthly inflation (YoY) and key rate, `InflationXML` method.

Funds reinvest dividends automatically, so price alone captures total return — no separate dividend handling needed. Real return uses the Fisher equation with monthly inflation derived from the YoY figure: `(1 + r)^(1/12) - 1`.

## Project structure

```
moex-fund-compare/
├── ingestion/
│   ├── moex.py
│   ├── cbr.py
│   └── fetch_all_prices.py
├── dbt/
│   └── models/{staging,mart}/
├── airflow/dags/moex_analytics.py
├── api/
│   ├── main.py
│   └── templates/index.html
├── infra/
│   ├── bootstrap.sh
│   ├── Dockerfile.airflow
│   ├── Dockerfile.api
│   ├── sql/
│   └── terraform/
├── docker-compose.yml
└── .env.example
```

## Running locally

```bash
git clone https://github.com/stubchief/moex-fund-compare
cd moex-fund-compare
cp .env.example .env
mkdir -p airflow/dags
docker compose up --build
```

- Dashboard: http://localhost:8000
- Airflow: http://localhost:8080 (admin / admin)

Trigger the pipeline manually if you don't want to wait for the daily schedule:

```bash
docker compose exec airflow-webserver airflow dags unpause moex_etf_pipeline
docker compose exec airflow-webserver airflow dags trigger moex_etf_pipeline
```

`.env` needed for local use:

```bash
POSTGRES_USER=airflow
POSTGRES_PASSWORD=airflow
POSTGRES_DB=airflow_db
AIRFLOW_UID=50000
```

## Deployment

Terraform support currently targets **Yandex Cloud only**. It provisions a single VM and, via `cloud-init`, also runs the first deploy: generates `.env` on the VM, clones this repo, runs `docker compose up --build -d`, then unpauses and triggers the DAG.

`.env` additionally needs, for Terraform:

```bash
TF_VAR_folder_id=                 # `yc config list`
TF_VAR_ssh_public_key_path=       # e.g. /home/you/.ssh/id_rsa.pub
TF_VAR_postgres_user=airflow      # same as POSTGRES_USER above
TF_VAR_postgres_password=airflow  # same as POSTGRES_PASSWORD above
```

```bash
# one-time: service account for Terraform (reads TF_VAR_folder_id from .env)
./infra/bootstrap.sh

# provision — must source .env first, terraform reads TF_VAR_* from the environment
set -a; source .env; set +a
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform apply
```

`vm_external_ip` comes out as a Terraform output. Give it a minute or two to finish building images, then hit `:8000` and `:8080` same as local.

Nothing watches the repo for changes — `cloud-init` only runs on first boot. Redeploying after a code change is manual:

```bash
ssh ubuntu@<vm_external_ip>
cd /opt/moex-etf && git pull && docker compose up --build -d
```

## API

| Method | Path | Description |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/api/funds` | Fund reference list |
| GET | `/api/top-funds` | Top N by real return (`?months=12&limit=10`) |
| GET | `/api/performance` | Monthly series for tickers (`?tickers=SBMX,AKGD&from=...&to=...`) |
| GET | `/api/calculator` | Investment calculator (`?ticker=SBMX&amount=100000&from=...&to=...`) |

Docs at `/docs`.

## Notes on a few choices

- **Postgres, not ClickHouse** — ~100 funds × ~1500 trading days doesn't need a columnar store.
- **Compose, not k8s** — single node, no scaling need.
- **raw stores TEXT** — casting and dedup happen in dbt staging, so raw stays a safe, idempotent source of truth.