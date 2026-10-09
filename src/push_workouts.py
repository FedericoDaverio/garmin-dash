"""
push_workouts.py
----------------
Recibe el plan generado por el Coach IA (variable PLAN_JSON), crea cada
entrenamiento en Garmin Connect con objetivos por ZONA (potencia o FC) y lo
programa en el calendario. Desde ahí se sincroniza solo al Edge/reloj para
entrenamiento guiado.

    PLAN_JSON='[{"date":"2026-10-12","sport":"cycling",...}]' \
    GARMIN_EMAIL=... GARMIN_PASSWORD=... python src/push_workouts.py

Formato de cada entrenamiento:
    {"date": "YYYY-MM-DD" | null, "sport": "cycling" | "running",
     "name": "...", "notes": "...", "mode": "power" | "hr",
     "items": [ {"kind": "warmup|work|recovery|rest|cooldown|active",
                 "minutes": 10, "zone": 1},          # o "km" en vez de "minutes"
                {"repeat": 4, "steps": [ {...}, {...} ]} ]}
"""

import json
import os
import re
import sys

# kind -> (stepTypeId, stepTypeKey, displayOrder), igual que garminconnect.workout
STEP_TYPES = {
    "warmup": (1, "warmup", 1),
    "cooldown": (2, "cooldown", 2),
    "work": (3, "interval", 3),
    "interval": (3, "interval", 3),
    "active": (3, "interval", 3),
    "recovery": (4, "recovery", 4),
    "rest": (5, "rest", 5),
}
SPORTS = {
    "cycling": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
    "running": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
}
TARGET_NONE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
TARGET_POWER_ZONE = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone", "displayOrder": 1}
TARGET_HR_ZONE = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 1}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_DISTANCE = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
END_ITERATIONS = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Builder:
    """Convierte los items del plan en la estructura JSON de Garmin, numerando
    stepOrder de forma consecutiva (los pasos dentro de un repeat también)."""

    def __init__(self, sport: str, mode: str):
        self.sport = sport
        self.mode = "power" if (mode == "power" and sport == "cycling") else "hr"
        self.order = 0
        self.seconds = 0.0

    def _step(self, s: dict):
        kind = str(s.get("kind", "active")).lower()
        type_id, type_key, display = STEP_TYPES.get(kind, STEP_TYPES["active"])
        minutes, km = float(s.get("minutes") or 0), float(s.get("km") or 0)
        if minutes > 0:
            end, end_value = END_TIME, round(minutes * 60)
            self.seconds += minutes * 60
        elif km > 0 and self.sport == "running":
            end, end_value = END_DISTANCE, round(km * 1000)
            self.seconds += km * 6 * 60  # estimación ~6 min/km, solo para la duración total
        else:
            return None
        zone = int(round(float(s.get("zone") or 0)))
        step = {
            "type": "ExecutableStepDTO",
            "stepOrder": None,
            "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key, "displayOrder": display},
            "endCondition": end,
            "endConditionValue": end_value,
            "targetType": TARGET_NONE,
        }
        if zone:
            if self.mode == "power":
                step["targetType"], step["zoneNumber"] = TARGET_POWER_ZONE, clamp(zone, 1, 7)
            else:
                step["targetType"], step["zoneNumber"] = TARGET_HR_ZONE, clamp(zone, 1, 5)
        self.order += 1
        step["stepOrder"] = self.order
        return step

    def build(self, items: list) -> list:
        out = []
        for it in items:
            if isinstance(it, dict) and it.get("repeat") and isinstance(it.get("steps"), list):
                self.order += 1
                group_order = self.order
                inner_start_seconds = self.seconds
                inner = [s for s in (self._step(x) for x in it["steps"] if isinstance(x, dict)) if s]
                if not inner:
                    self.order = group_order - 1
                    continue
                n = clamp(int(it["repeat"]), 2, 50)
                self.seconds += (self.seconds - inner_start_seconds) * (n - 1)
                out.append({
                    "type": "RepeatGroupDTO",
                    "stepOrder": group_order,
                    "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                    "numberOfIterations": n,
                    "workoutSteps": inner,
                    "endCondition": END_ITERATIONS,
                    "endConditionValue": float(n),
                    "smartRepeat": False,
                })
            elif isinstance(it, dict):
                s = self._step(it)
                if s:
                    out.append(s)
        return out


def build_garmin_workout(w: dict) -> dict:
    sport = "running" if str(w.get("sport", "")).lower().startswith("run") else "cycling"
    b = Builder(sport, str(w.get("mode", "hr")))
    steps = b.build(w.get("items") or [])
    if not steps:
        raise ValueError("el entrenamiento no tiene pasos válidos")
    name = str(w.get("name") or "Entrenamiento")[:60]
    payload = {
        "workoutName": name,
        "sportType": SPORTS[sport],
        "estimatedDurationInSecs": int(b.seconds),
        "workoutSegments": [{"segmentOrder": 1, "sportType": SPORTS[sport], "workoutSteps": steps}],
    }
    if w.get("notes"):
        payload["description"] = str(w["notes"])[:512]
    return payload


def main() -> int:
    raw = os.environ.get("PLAN_JSON", "").strip()
    if not raw:
        print("PLAN_JSON vacío: no hay nada que enviar.")
        return 1
    plan = json.loads(raw)
    if isinstance(plan, dict):
        plan = plan.get("workouts", [])
    if not isinstance(plan, list) or not plan:
        print("El plan no contiene entrenamientos.")
        return 1

    from garminconnect import Garmin

    client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    client.login()

    ok, failed = 0, 0
    for w in plan:
        label = f"{w.get('date') or 'sin fecha'} · {w.get('name')}"
        try:
            result = client.upload_workout(build_garmin_workout(w))
            workout_id = result.get("workoutId")
            if not workout_id:
                raise RuntimeError(f"Garmin no devolvió workoutId: {str(result)[:200]}")
            date = str(w.get("date") or "")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                client.schedule_workout(workout_id, date)
                print(f"OK  {label} -> workout {workout_id} programado el {date}")
            else:
                print(f"OK  {label} -> workout {workout_id} (sin fecha, queda en tu biblioteca)")
            ok += 1
        except Exception as e:  # un entrenamiento fallido no debe frenar los demás
            failed += 1
            print(f"ERR {label}: {e}")
    print(f"\nEnviados: {ok}  Fallidos: {failed}")
    return 0 if ok and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
