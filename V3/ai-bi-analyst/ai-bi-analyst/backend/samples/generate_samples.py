"""Generate synthetic datasets with known, deliberate data-quality problems.

    python samples/generate_samples.py

Writes retail_orders.csv, subscription_churn.csv and retail_orders.xlsx next to
this file. Every defect is intentional so the profiler's findings can be checked
against a known answer.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RNG = np.random.default_rng(20240917)


def retail_orders(n: int = 4000) -> pd.DataFrame:
    regions = ["North", "South", "East", "West", "north ", "SOUTH"]  # casing + whitespace variants
    channels = ["Online", "Retail", "Partner", "Wholesale"]
    categories = ["Beverages", "Snacks", "Dairy", "Frozen", "Household", "Produce", "Bakery"]
    dates = pd.to_datetime("2023-01-01") + pd.to_timedelta(RNG.integers(0, 700, n), unit="D")

    region = RNG.choice(regions, n, p=[0.3, 0.27, 0.2, 0.17, 0.03, 0.03])
    channel = RNG.choice(channels, n, p=[0.45, 0.3, 0.15, 0.1])
    category = RNG.choice(categories, n)
    units = RNG.integers(1, 40, n)
    unit_price = np.round(RNG.gamma(4.0, 6.0, n) + 2, 2)
    seasonal = 1 + 0.25 * np.sin(dates.dayofyear / 365 * 2 * np.pi)
    channel_lift = pd.Series(channel).map({"Online": 1.18, "Retail": 1.0, "Partner": 0.9, "Wholesale": 1.35}).to_numpy()
    revenue = np.round(units * unit_price * seasonal * channel_lift, 2)
    discount_pct = np.round(np.clip(RNG.normal(8, 5, n), 0, 45), 2)
    margin = np.round(revenue * (0.34 - discount_pct / 400) + RNG.normal(0, 4, n), 2)

    df = pd.DataFrame(
        {
            "order_id": [f"ORD-{100000 + i}" for i in range(n)],
            "order_date": dates,
            "ship_date": dates + pd.to_timedelta(RNG.integers(0, 12, n), unit="D"),
            "customer_id": RNG.integers(10_000, 12_500, n),
            "customer_email": [f"user{RNG.integers(1, 3000)}@example.com" for _ in range(n)],
            "region": region,
            "country": RNG.choice(["United States", "Canada", "Mexico"], n, p=[0.7, 0.2, 0.1]),
            "postal_code": [f"{RNG.integers(10000, 99999)}" for _ in range(n)],
            "sales_channel": channel,
            "product_category": category,
            "units_sold": units,
            "unit_price": unit_price,
            "revenue_amount": revenue,
            "discount_pct": discount_pct,
            "margin_amount": margin,
            "delivery_days": RNG.integers(1, 14, n),
            "satisfaction_score": np.round(np.clip(RNG.normal(4.1, 0.7, n), 1, 5), 1),
            "currency_code": "USD",  # constant column
            "order_notes": [
                RNG.choice(
                    [
                        "Customer requested split delivery across two addresses.",
                        "Escalated to the regional service desk for a damaged pallet.",
                        "Standard order, no special handling requested by the buyer.",
                        "",
                    ]
                )
                for _ in range(n)
            ],
        }
    )

    # Seeded defects -------------------------------------------------------
    df.loc[RNG.choice(n, 160, replace=False), "margin_amount"] = np.nan        # missing values
    df.loc[RNG.choice(n, 90, replace=False), "satisfaction_score"] = np.nan
    df.loc[RNG.choice(n, 35, replace=False), "region"] = None
    df.loc[RNG.choice(n, 25, replace=False), "revenue_amount"] *= 28           # extreme outliers
    df.loc[RNG.choice(n, 18, replace=False), "units_sold"] *= -1               # sign-convention problem
    df.loc[RNG.choice(n, 12, replace=False), "order_date"] = pd.Timestamp("2031-06-15")  # future dates
    df.loc[RNG.choice(n, 30, replace=False), "delivery_days"] = 0
    df.loc[RNG.choice(n, 40, replace=False), "postal_code"] = " 90210 "        # stray whitespace
    df = pd.concat([df, df.sample(45, random_state=7)], ignore_index=True)      # duplicate rows
    df["fiscal_period"] = df["order_date"].dt.strftime("%Y-%m")                 # date stored as text
    return df.sample(frac=1, random_state=11).reset_index(drop=True)


def subscription_churn(n: int = 2600) -> pd.DataFrame:
    plans = ["Starter", "Growth", "Scale", "Enterprise"]
    plan = RNG.choice(plans, n, p=[0.42, 0.31, 0.19, 0.08])
    tenure = RNG.integers(1, 48, n)
    seats = np.clip(RNG.poisson(pd.Series(plan).map({"Starter": 3, "Growth": 12, "Scale": 45, "Enterprise": 180})), 1, None)
    mrr = np.round(seats * pd.Series(plan).map({"Starter": 12, "Growth": 18, "Scale": 24, "Enterprise": 31}).to_numpy() * RNG.normal(1, 0.12, n), 2)
    tickets = RNG.poisson(np.clip(6 - tenure / 10, 0.4, None))
    logins = np.clip(RNG.normal(24 - tickets * 1.6, 7, n).round(), 0, None)
    churn_p = np.clip(0.34 - tenure * 0.006 + tickets * 0.035 - logins * 0.004, 0.01, 0.9)

    return pd.DataFrame(
        {
            "account_id": [f"ACC-{2000 + i}" for i in range(n)],
            "signup_date": pd.to_datetime("2021-01-01") + pd.to_timedelta(RNG.integers(0, 1500, n), unit="D"),
            "plan_tier": plan,
            "industry": RNG.choice(["Retail", "Finance", "Healthcare", "Manufacturing", "Public sector", "Education"], n),
            "seats": seats,
            "monthly_recurring_revenue": mrr,
            "tenure_months": tenure,
            "support_tickets_90d": tickets,
            "monthly_logins": logins,
            "nps_score": np.clip(RNG.normal(31, 22, n).round(), -100, 100),
            "churned": RNG.binomial(1, churn_p),
            "renewal_date": pd.to_datetime("2025-01-01") + pd.to_timedelta(RNG.integers(0, 500, n), unit="D"),
        }
    )


def main() -> None:
    orders = retail_orders()
    churn = subscription_churn()
    orders.to_csv(HERE / "retail_orders.csv", index=False)
    churn.to_csv(HERE / "subscription_churn.csv", index=False)
    with pd.ExcelWriter(HERE / "retail_orders.xlsx", engine="openpyxl") as writer:
        orders.head(1500).to_excel(writer, sheet_name="Orders", index=False)
        churn.head(800).to_excel(writer, sheet_name="Accounts", index=False)
    print(f"retail_orders.csv       {orders.shape[0]:>6,} rows x {orders.shape[1]} cols")
    print(f"subscription_churn.csv  {churn.shape[0]:>6,} rows x {churn.shape[1]} cols")
    print("retail_orders.xlsx      two sheets: Orders, Accounts")


if __name__ == "__main__":
    main()
