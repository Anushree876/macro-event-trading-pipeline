import json
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd
from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv()

DATA = Path("data")
DATA.mkdir(exist_ok=True)
RAW_PATH = DATA / "calendar_raw.json"
DB_PATH = DATA / "macro.db"

ACTOR_ID = "xtracto/forexfactory-calendar"
ACTOR_INPUT = {
    "dateRange": "range",
    "rangeFrom": "2026-04-15",
    "rangeTo": "2026-10-12",
    "currencies": ["USD"],
    "minImpact": "medium",
    "maxItems": 0,
}

MULT = {"": 1, "%": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def fetch_calendar():
    """Run the Apify actor once; reuse the saved raw file afterwards."""
    if RAW_PATH.exists():
        print("Using cached raw calendar")
        return json.loads(RAW_PATH.read_text(encoding="utf-8"))
    client = ApifyClient(os.getenv("APIFY_TOKEN"))
    run = client.actor(ACTOR_ID).call(run_input=ACTOR_INPUT)
    items = list(client.dataset(run.default_dataset_id).iterate_items())
    RAW_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")
    print(f"Fetched {len(items)} raw rows")
    return items


def parse_num(s):
    """'197K' -> 197000, '4.7%' -> 4.7, '' -> NaN."""
    if s is None:
        return float("nan")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)([KMBT%]?)", str(s).strip())
    if not m:
        return float("nan")
    return float(m.group(1)) * MULT[m.group(2)]


def clean_calendar(raw):
    df = pd.DataFrame(raw)
    df["ts_utc"] = pd.to_datetime(df["datetimeISO"], utc=True)
    df["ts_et"] = df["ts_utc"].dt.tz_convert("America/New_York")
    for col in ["actual", "forecast", "previous"]:
        df[col + "_num"] = df[col].map(parse_num)
    df = df.dropna(subset=["actual_num", "forecast_num"])
    df["surprise"] = (df["actual_num"] - df["forecast_num"]).round(4)
    df = df.drop_duplicates(subset=["id"]).sort_values("ts_utc")
    df = df.rename(columns={"title": "name"})
    df["ts_utc"] = df["ts_utc"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df["ts_et"] = df["ts_et"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df[["id", "ts_utc", "ts_et", "name", "impact", "actual_num",
               "forecast_num", "previous_num", "surprise"]]


def save_events(df):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            ts_utc TEXT, ts_et TEXT, name TEXT, impact TEXT,
            actual REAL, forecast REAL, previous REAL, surprise REAL,
            UNIQUE (ts_utc, name)
        )""")
    con.executemany(
        "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
        df.itertuples(index=False, name=None),
    )
    con.commit()
    con.close()


def ingest_calendar():
    df = clean_calendar(fetch_calendar())
    save_events(df)
    print(f"Saved {len(df)} events to {DB_PATH}")
    return df


if __name__ == "__main__":
    ingest_calendar()