"""
build_data.py
-------------
Descarga TODAS las actividades y las métricas de bienestar/entrenamiento de
Garmin Connect y las guarda en docs/data/activities.json y
docs/data/wellness.json. El dashboard (docs/index.html) dibuja todo en el
navegador a partir de estos dos archivos.

    python src/build_data.py                  # datos de ejemplo
    USE_GARMIN=1 python src/build_data.py     # datos reales

Nota: varios campos (optimal range de training load, categorías de VO2max,
HRV status) vienen de endpoints no oficiales de Garmin y su forma exacta
puede variar por cuenta/dispositivo — el parseo es defensivo (safe_path) y
cae a None en vez de romper el build si un campo no existe.
"""

import json
import os
from datetime import date, datetime, timedelta

import numpy as np

ACTIVITIES_PATH = os.path.join("docs", "data", "activities.json")
WELLNESS_PATH = os.path.join("docs", "data", "wellness.json")

TODAY = date.today()


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def safe_path(d, *keys, default=None):
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and cur:
            cur = cur[0].get(k) if isinstance(cur[0], dict) else None
        else:
            return default
        if cur is None:
            return default
    return cur


def sportOf(type_key: str) -> str:
    t = (type_key or "").lower()
    if "cycl" in t or "bik" in t or "ride" in t:
        return "cycling"
    if "run" in t:
        return "running"
    return "other"


def first_device_block(d):
    """Varias respuestas de training status anidan los datos bajo un
    deviceId dinámico: {"123456": {...}}. Devuelve el primer bloque."""
    if not isinstance(d, dict) or not d:
        return {}
    v = next(iter(d.values()))
    return v if isinstance(v, dict) else {}


# ---------------------------------------------------------------------------
# Actividades
# ---------------------------------------------------------------------------

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
        "pw": round(a["avgPower"]) if a.get("avgPower") else None,
        "np": round(a["normPower"]) if a.get("normPower") else None,
    }


def fetch_activities(client):
    rows, start, size = [], 0, 100
    while True:
        batch = client.get_activities(start, size)
        if not batch:
            break
        rows.extend(_row(a) for a in batch)
        if len(batch) < size:
            break
        start += size
    return [r for r in rows if r["d"]]


def mock_activities():
    rng = np.random.default_rng(42)
    d = date(TODAY.year - 3, 1, 1)
    rows = []
    while d <= TODAY:
        if rng.random() < 0.5:
            t = rng.choice(["road_biking", "road_biking", "mountain_biking", "running", "strength_training"])
            season = 1 + 0.35 * np.sin((d.timetuple().tm_yday / 365) * 2 * np.pi - 1.4)
            pw = np_ = None
            if "biking" in t:
                km = max(rng.normal(45, 18), 8) * season
                spd = rng.normal(27, 3)
                elev = max(rng.normal(450, 200), 20) * season
                pw = int(max(rng.normal(175, 30), 90))
                np_ = pw + int(max(rng.normal(18, 8), 0))  # NP >= avg power
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
                "pw": pw, "np": np_,
            })
        d += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Wellness: training load, VO2max, FTP, sueño, pasos, estrés, intensidad
# ---------------------------------------------------------------------------

def fetch_training_load(client, candidate_dates, max_calls=120):
    """Garmin solo recalcula el training status/load los días en que hubo
    actividad — pedirlo para fechas arbitrarias casi siempre da null. Por
    eso se consulta en los días reales de actividad (más recientes primero)."""
    points, latest = [], {}
    for i, d in enumerate(candidate_dates[:max_calls]):
        try:
            data = client.get_training_status(d)
        except Exception:
            continue
        if not data:
            continue
        balance = safe_path(data, "mostRecentTrainingLoadBalance") or {}
        balance_block = first_device_block(balance.get("metricsTrainingLoadBalanceDTOMap", {})) or balance
        acute = safe_path(balance_block, "monthlyLoadAerobicLow") or safe_path(balance_block, "acuteTrainingLoad") \
            or safe_path(data, "dailyTrainingLoadAcute")
        chronic = safe_path(balance_block, "chronicTrainingLoad") or safe_path(data, "dailyTrainingLoadChronic")
        lo = safe_path(balance_block, "minTrainingLoadAcute")
        hi = safe_path(balance_block, "maxTrainingLoadAcute")
        status = safe_path(data, "mostRecentTrainingStatus") or {}
        status_block = first_device_block(status.get("latestTrainingStatusData", {})) or status
        phrase = safe_path(status_block, "trainingStatusFeedbackPhrase") or safe_path(status, "trainingStatusFeedbackPhrase")
        if acute is None and chronic is None and phrase is None:
            continue
        if acute is not None or chronic is not None:
            points.append({"d": d, "acute": acute, "chronic": chronic, "lo": lo, "hi": hi})
        if i == 0 or not latest:
            latest = {
                "phrase": phrase,
                "loadFocus": safe_path(balance, "trainingBalanceFeedbackPhrase"),
                "hrvStatus": safe_path(data, "hrvStatus") or safe_path(data, "mostRecentVO2Max", "hrvStatus"),
            }
    points.sort(key=lambda p: p["d"])
    # banda óptima aproximada cuando Garmin no la expone: ACWR 0.8–1.3 sobre la carga crónica
    for p in points:
        if (p["lo"] is None or p["hi"] is None) and p.get("chronic"):
            p["lo"], p["hi"] = round(p["chronic"] * 0.8), round(p["chronic"] * 1.3)
    return points, latest


def fetch_vo2max_for(client, dates, field, max_calls=80):
    """field: 'generic' (running) o 'cycling'."""
    points = []
    for d in dates[:max_calls]:
        try:
            data = client.get_max_metrics(d)
        except Exception:
            continue
        if not data:
            continue
        entry = data[0] if isinstance(data, list) else data
        v = safe_path(entry, field, "vo2MaxPreciseValue") or safe_path(entry, field, "vo2MaxValue")
        if v:
            points.append({"d": d, "v": v})
    points.sort(key=lambda p: p["d"])
    return points


def fetch_vo2max(client, run_dates, bike_dates):
    run_points = fetch_vo2max_for(client, run_dates, "generic")
    bike_points = fetch_vo2max_for(client, bike_dates, "cycling")
    by_date = {}
    for p in run_points:
        by_date.setdefault(p["d"], {"d": p["d"]})["run"] = p["v"]
    for p in bike_points:
        by_date.setdefault(p["d"], {"d": p["d"]})["bike"] = p["v"]
    points = sorted(by_date.values(), key=lambda p: p["d"])
    category = None
    last_v = (run_points[-1]["v"] if run_points else None) or (bike_points[-1]["v"] if bike_points else None)
    if last_v:
        # categorías aproximadas (Garmin ajusta por edad/sexo; esto es solo una referencia general)
        category = "Excelente" if last_v >= 55 else "Bueno" if last_v >= 45 else "Regular" if last_v >= 35 else "Bajo"
    return points, category


def fetch_ftp(client):
    try:
        data = client.get_cycling_ftp()
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    v = data.get("functionalThresholdPower")
    d = (data.get("calendarDate") or "")[:10] or TODAY.isoformat()
    return [{"d": d, "ftp": v}] if v else []


def fetch_sleep(client, days=90):
    points = []
    for i in range(days):
        d = TODAY - timedelta(days=i)
        try:
            data = client.get_sleep_data(d.isoformat())
        except Exception:
            continue
        secs = safe_path(data, "dailySleepDTO", "sleepTimeSeconds")
        if secs:
            points.append({"d": d.isoformat(), "h": round(secs / 3600, 2)})
    points.sort(key=lambda p: p["d"])
    return points


def fetch_weekly_steps(client):
    try:
        data = client.get_weekly_steps()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for w in data:
        d = w.get("calendarDate") or w.get("weekStart") or w.get("date")
        steps = w.get("totalSteps") or w.get("averageSteps") or w.get("steps")
        if d and steps is not None:
            out.append({"d": str(d)[:10], "steps": steps})
    out.sort(key=lambda p: p["d"])
    return out


def fetch_weekly_stress(client):
    try:
        data = client.get_weekly_stress()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for w in data:
        d = w.get("calendarDate") or w.get("weekStart") or w.get("date")
        stress = w.get("overallStressLevel") or w.get("avgStressLevel") or w.get("averageStressLevel")
        if d and stress is not None:
            out.append({"d": str(d)[:10], "stress": stress})
    out.sort(key=lambda p: p["d"])
    return out


def fetch_intensity_minutes(client, weeks=52):
    end, start = TODAY, TODAY - timedelta(weeks=weeks)
    try:
        data = client.get_weekly_intensity_minutes(start.isoformat(), end.isoformat())
    except Exception:
        data = None
    out = []
    if isinstance(data, list):
        for w in data:
            d = w.get("calendarDate") or w.get("weekStart") or w.get("date")
            mins = w.get("weeklyGoalMinutes") or w.get("moderateValue") or w.get("totalMinutes")
            if d and mins is not None:
                out.append({"d": str(d)[:10], "min": mins})
    out.sort(key=lambda p: p["d"])
    return out


def mock_wellness():
    rng = np.random.default_rng(11)

    tl = []
    base = 60 + 20 * np.sin(np.linspace(0, 6, 52))
    for i in range(52):
        d = TODAY - timedelta(days=7 * (51 - i))
        chronic = base[i]
        acute = chronic + rng.normal(0, 10)
        tl.append({"d": d.isoformat(), "acute": round(acute), "chronic": round(chronic),
                   "lo": round(chronic * 0.8), "hi": round(chronic * 1.3)})
    latest_status = {"phrase": "MAINTAINING", "loadFocus": "BALANCED", "hrvStatus": "BALANCED"}

    vo2 = []
    for i in range(26):
        d = TODAY - timedelta(days=14 * (25 - i))
        vo2.append({"d": d.isoformat(), "run": round(47 + i * 0.1 + rng.normal(0, 0.3), 1),
                    "bike": round(50 + i * 0.12 + rng.normal(0, 0.3), 1), "fitnessAge": round(33 - i * 0.05)})

    ftp = [{"d": (TODAY - timedelta(days=30 * (5 - i))).isoformat(), "ftp": v}
           for i, v in enumerate([210, 215, 218, 222, 228, 231])]

    sleep = [{"d": (TODAY - timedelta(days=i)).isoformat(), "h": round(max(rng.normal(7, 0.8), 4), 1)}
             for i in range(90)][::-1]

    steps = [{"d": (TODAY - timedelta(weeks=52 - i)).isoformat(), "steps": int(max(rng.normal(8500, 2000), 2000))}
             for i in range(52)]
    stress = [{"d": (TODAY - timedelta(weeks=52 - i)).isoformat(), "stress": int(np.clip(rng.normal(32, 10), 5, 80))}
              for i in range(52)]
    intensity = [{"d": (TODAY - timedelta(weeks=52 - i)).isoformat(), "min": int(max(rng.normal(150, 60), 0))}
                 for i in range(52)]

    return {
        "training_load": tl, "training_status": latest_status,
        "vo2max": vo2, "vo2max_category": "Bueno", "ftp": ftp,
        "sleep": sleep, "steps_weekly": steps, "stress_weekly": stress,
        "intensity_minutes": intensity,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def debug_dump(client):
    """Imprime la respuesta cruda de los endpoints problemáticos, usando
    fechas reales de actividad (no 'hoy') para darles la mejor oportunidad
    de traer datos. No escribe ningún archivo."""
    activities = fetch_activities(client)
    cutoff = (TODAY - timedelta(days=365)).isoformat()
    recent = [a for a in activities if a["d"] >= cutoff]
    last_any = recent[-1]["d"] if recent else TODAY.isoformat()
    last_run = next((a["d"] for a in reversed(recent) if sportOf(a["t"]) == "running"), None)
    last_bike = next((a["d"] for a in reversed(recent) if sportOf(a["t"]) == "cycling"), None)

    print(f"last_any={last_any} last_run={last_run} last_bike={last_bike}")

    calls = {
        f"get_training_status({last_any})": lambda: client.get_training_status(last_any),
        f"get_max_metrics({last_run}) [running]": (lambda: client.get_max_metrics(last_run)) if last_run else None,
        f"get_max_metrics({last_bike}) [cycling]": (lambda: client.get_max_metrics(last_bike)) if last_bike else None,
        "get_cycling_ftp()": lambda: client.get_cycling_ftp(),
        "get_weekly_steps()": lambda: client.get_weekly_steps(),
        "get_weekly_stress()": lambda: client.get_weekly_stress(),
        "get_weekly_intensity_minutes(...)": lambda: client.get_weekly_intensity_minutes(
            (TODAY - timedelta(weeks=52)).isoformat(), TODAY.isoformat()),
    }
    for label, fn in calls.items():
        print(f"\n{'=' * 20} {label} {'=' * 20}")
        if fn is None:
            print("SKIPPED: no hay actividad de ese tipo en los últimos 365 días")
            continue
        try:
            result = fn()
            text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
            print(text[:4000] + ("\n... [truncado]" if len(text) > 4000 else ""))
        except Exception as e:
            print(f"ERROR: {e}")


def main():
    use_garmin = os.environ.get("USE_GARMIN") == "1"
    only_activities = os.environ.get("ONLY_ACTIVITIES") == "1"
    debug = os.environ.get("DEBUG_WELLNESS") == "1"

    if debug:
        if not use_garmin:
            print("DEBUG_WELLNESS requiere USE_GARMIN=1")
            return
        from garminconnect import Garmin
        client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
        client.login()
        debug_dump(client)
        return

    if use_garmin:
        from garminconnect import Garmin
        client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
        client.login()
        try:
            athlete = client.get_full_name() or ""
        except Exception:
            athlete = ""

        activities = fetch_activities(client)

        if only_activities:
            wellness = None  # no se toca wellness.json en un refresco rápido
        else:
            cutoff = (TODAY - timedelta(days=365)).isoformat()
            all_dates = sorted({a["d"] for a in activities if a["d"] >= cutoff}, reverse=True)
            run_dates = sorted({a["d"] for a in activities if a["d"] >= cutoff and sportOf(a["t"]) == "running"}, reverse=True)
            bike_dates = sorted({a["d"] for a in activities if a["d"] >= cutoff and sportOf(a["t"]) == "cycling"}, reverse=True)

            training_load, training_status = fetch_training_load(client, all_dates)
            vo2max, vo2max_category = fetch_vo2max(client, run_dates, bike_dates)
            wellness = {
                "training_load": training_load, "training_status": training_status,
                "vo2max": vo2max, "vo2max_category": vo2max_category,
                "ftp": fetch_ftp(client),
                "sleep": fetch_sleep(client),
                "steps_weekly": fetch_weekly_steps(client),
                "stress_weekly": fetch_weekly_stress(client),
                "intensity_minutes": fetch_intensity_minutes(client),
            }
    else:
        athlete = "Atleta de ejemplo"
        activities = mock_activities()
        wellness = None if only_activities else mock_wellness()

    activities.sort(key=lambda r: r["d"])
    os.makedirs(os.path.dirname(ACTIVITIES_PATH), exist_ok=True)
    with open(ACTIVITIES_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"athlete": athlete, "generated": datetime.now().isoformat(timespec="minutes"),
             "activities": activities},
            f, ensure_ascii=False, separators=(",", ":"),
        )
    print(f"{len(activities)} actividades -> {ACTIVITIES_PATH}")

    if wellness is not None:
        with open(WELLNESS_PATH, "w", encoding="utf-8") as f:
            json.dump(wellness, f, ensure_ascii=False, separators=(",", ":"))
        print(f"wellness -> {WELLNESS_PATH}")
    else:
        print("wellness -> sin cambios (refresco solo de actividades)")


if __name__ == "__main__":
    main()
