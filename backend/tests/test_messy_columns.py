"""Messy columns are repaired generically: currency text, mixed date formats, label variants and exact duplicates."""
import pandas as pd

from app.cleaner import coerce_types
from app.profiler import profile_df


def messy():
    n = 60
    price = [f"Rs. {1000 + i:,}" if i % 4 == 0 else str(1000 + i) for i in range(n)]
    price[1] = "Rs.2,500"
    # three formats; dates run Apr 2023 - Mar 2025 and the slash dates include a day above 12, so the column is day-first
    months = pd.period_range("2023-04", "2025-03", freq="M")
    dates, truth = [], []
    for i in range(n):
        d = months[i % len(months)].to_timestamp() + pd.Timedelta(days=i % 10)
        truth.append(d)
        dates.append([d.strftime("%Y-%m-%d"), d.strftime("%d/%m/%Y"), d.strftime("%d-%b-%Y")][i % 3])
    brand = ["Raymond" if i % 3 else ("raymond " if i % 2 else "Allen  Solly") for i in range(n)]
    brand[0] = "Allen Solly"
    df = pd.DataFrame({"Bill": [f"B{i:03d}" for i in range(n)], "Date": dates, "Brand": brand, "Price": price, "Qty": range(1, n + 1)})
    df.loc[n - 1, "Date"] = "25/03/2025"; truth[-1] = pd.Timestamp("2025-03-25")      # the one slash date with a day above 12 settles the order
    messy.truth = pd.Series(truth)
    return pd.concat([df, df.iloc[:5]], ignore_index=True)       # 5 exact duplicates


def test_all_four_messes_are_repaired_and_logged():
    raw = messy()
    out, log = coerce_types(raw, profile_df(raw))
    text = {s["step"]: s["detail"] for s in log}

    assert pd.api.types.is_numeric_dtype(out["Price"])
    assert out["Price"].iloc[0] == 1000 and out["Price"].iloc[1] == 2500 and out["Price"].notna().all()
    assert "converted" in text["Convert to number"] and "Price" in text["Convert to number"]

    assert (out["Date"].iloc[:60].reset_index(drop=True) == messy.truth).all()
    assert out["Date"].min() == pd.Timestamp("2023-04-01") and out["Date"].max() == pd.Timestamp("2025-03-25")
    assert out["Date"].notna().all()
    assert "day-first" in text["Convert to date"]

    assert set(out["Brand"]) == {"Raymond", "Allen Solly"}
    assert "Merge labels" in text and "Brand" in text["Merge labels"]

    assert len(out) == len(raw) - 5
    assert "5" in text["Remove duplicates"]
