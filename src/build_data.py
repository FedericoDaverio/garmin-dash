"""
build_data.py
-------------
Descarga TODAS las actividades de Garmin Connect y las guarda en
docs/data/activities.json. El dashboard (docs/index.html) lee ese archivo
y dibuja todo en el navegador: KPIs, pestañas, gráficas, CSV y contexto
para el coach IA.

    python src/build_data.py                  # datos de ejemplo
    USE_GARMIN=1 python src/build_data.py     # datos reales
"""

import json
import os
from datetime import date, datetime, timedelta

import numpy as np

OUTPUT_PATH = os.path.join("docs", "data", "activities.json")


def _row(a: dict) -> dict:
    moving = a.get("movingDuration") or a.get("duration") or 0
    speed = a.get("averageSpeed")
    return {
        "d": (a.get("startTimeLocal") or "")[:10],
        "t": (a.get("activityType") or {}).get("typeKey", "other"),
        "n": a.get("activityName") or "",
        "km": round((a.get("distance") or 0) / 1000, 2),
        "elev": round(a.get("elevationGain") or 0),
        "h": round(moving / 3600, 3),
        "hr": round(a["averageHR"]) if a.get("averageHR") else None,
        "spd": round(speed * 3.6, 1) if speed else None,
    }


def fetch_garmin():
    from garminconnect import Garmin

    client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    client.login()
    try:
        athlete = client.get_full_name() or ""
    except Exception:
        athlete = ""

    rows, start, size = [], 0, 100
    while True:
        batch = client.get_activities(start, size)
        if not batch:
            break
        rows.extend(_row(a) for a in batch)
        if len(batch) < size:
            break
        start += size
    return athlete, [r for r in rows if r["d"]]


def mock_data():
    rng = np.random.default_rng(42)
    today = date.today()
    d = date(today.year - 3, 1, 1)
    rows = []
    while d <= today:
        if rng.random() < 0.5:
            t = rng.choice(["road_biking", "road_biking", "mountain_biking", "running", "strength_training"])
            season = 1 + 0.35 * np.sin((d.timetuple().tm_yday / 365) * 2 * np.pi - 1.4)
            if "biking" in t:
                km = max(rng.normal(45, 18), 8) * season
                spd = rng.normal(27, 3)
                elev = max(rng.normal(450, 200), 20) * season
            elif t == "running":
                km = max(rng.normal(9, 3), 3)
                spd = rng.normal(11, 1)
                elev = max(rng.normal(80, 40), 0)
            else:
                km, spd, elev = 0, None, 0
            h = km / spd if spd else rng.normal(0.9, 0.2)
            rows.append({
                "d": d.isoformat(), "t": t, "n": "Actividad de ejemplo",
                "km": round(km, 2), "elev": round(elev), "h": round(h, 3),
                "hr": int(rng.normal(142, 10)), "spd": round(spd, 1) if spd else None,
            })
        d += timedelta(days=1)
    return "Atleta de ejemplo", rows


def main():
    if os.environ.get("USE_GARMIN") == "1":
        athlete, rows = fetch_garmin()
    else:
        athlete, rows = mock_data()

    rows.sort(key=lambda r: r["d"])
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"athlete": athlete, "generated": datetime.now().isoformat(timespec="minutes"),
             "activities": rows},
            f, ensure_ascii=False, separators=(",", ":"),
        )
    print(f"{len(rows)} actividades -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
