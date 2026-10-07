"""
build_yearly_totals.py
-----------------------
Genera docs/yearly_totals.html: página estática con 3 pestañas
(Ciclismo / Carrera / General) mostrando distancia acumulada por año (YTD),
desnivel positivo mensual, horas de actividad mensual, y un gauge de avance
vs. el año anterior (mismo día).

Mismo patrón que build_training_status.py: corre con mock data por default,
o con Garmin real si USE_GARMIN=1 y GARMIN_EMAIL/GARMIN_PASSWORD están en
el entorno.
"""

import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go

DARK_BG = "#0e1117"
CARD_BG = "#161b22"
COLORS = {
    "current_year": "#FFA630",
    "past_years": "#6b7280",
    "elevation": "#9AA0A6",
    "hours": "#4D96FF",
}

SPORT_MAP = {
    "Ciclismo": ["cycling", "road_biking", "mountain_biking", "gravel_cycling",
                 "indoor_cycling", "virtual_ride"],
    "Carrera": ["running", "trail_running", "treadmill_running"],
}

OUTPUT_PATH = "docs/yearly_totals.html"
LOOKBACK_YEARS = 3


# ---------------------------------------------------------------------------
# Datos: Garmin real
# ---------------------------------------------------------------------------

def fetch_real_activities(client, start: str, end: str) -> pd.DataFrame:
    rows = []
    start_idx = 0
    page_size = 100
    while True:
        batch = client.get_activities(start_idx, page_size)
        if not batch:
            break
        for a in batch:
            d = a.get("startTimeLocal", "")[:10]
            if d < start or d > end:
                continue
            rows.append({
                "date": d,
                "sport": a.get("activityType", {}).get("typeKey", "other"),
                "distance_km": (a.get("distance") or 0) / 1000,
                "elevation_gain_m": a.get("elevationGain") or 0,
                "duration_hours": (a.get("duration") or 0) / 3600,
            })
        if len(batch) < page_size:
            break
        start_idx += page_size
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    return df


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

def generate_mock_activities(years: list[int]) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = []
    sports = (
        [("Ciclismo", "cycling")] * 3
        + [("Carrera", "running")] * 2
        + [("General", "other")] * 1
    )
    today = date.today()
    for year in years:
        last_day = min(date(year, 12, 31), today) if year == today.year else date(year, 12, 31)
        n_days = (last_day - date(year, 1, 1)).days + 1
        for day_offset in range(n_days):
            if rng.random() > 0.55:
                continue
            label, key = sports[rng.integers(0, len(sports))]
            seasonal = 1 + 0.4 * np.sin((day_offset / 365) * 2 * np.pi - 1.4)
            rows.append({
                "date": pd.Timestamp(date(year, 1, 1) + timedelta(days=day_offset)),
                "sport": key,
                "distance_km": max(rng.normal(35, 15), 3) * seasonal,
                "elevation_gain_m": max(rng.normal(350, 150), 0) * seasonal,
                "duration_hours": max(rng.normal(1.6, 0.6), 0.2),
            })
    return pd.DataFrame(rows)


def classify(df: pd.DataFrame, labels: list[str] | None) -> pd.DataFrame:
    if labels is None:
        return df
    keys = sum((SPORT_MAP[l] for l in labels if l in SPORT_MAP), [])
    return df[df["sport"].isin(keys)]


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------

def cumulative_by_year(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    if df.empty or metric not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d["year"] = d["date"].dt.year
    d["doy"] = d["date"].dt.dayofyear
    pivot = d.pivot_table(index="doy", columns="year", values=metric, aggfunc="sum").fillna(0)
    return pivot.cumsum()


def ytd_progress_pct(cum: pd.DataFrame, current_year: int, previous_year: int):
    if cum.empty or current_year not in cum.columns or previous_year not in cum.columns:
        return None
    last_doy = cum[current_year].last_valid_index()
    if last_doy is None:
        return None
    current_val = cum.loc[last_doy, current_year]
    target_val = cum.loc[:last_doy, previous_year].max()
    if target_val <= 0:
        return None
    return round(100 * current_val / target_val, 1)


def monthly_totals(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    if df.empty or metric not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d["year"] = d["date"].dt.year
    d["month"] = d["date"].dt.month
    return d.pivot_table(index="month", columns="year", values=metric, aggfunc="sum").fillna(0)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def cumulative_fig(cum: pd.DataFrame, title: str, unit: str):
    fig = go.Figure()
    years = sorted(cum.columns) if not cum.empty else []
    x_vals = cum.index.tolist() if not cum.empty else []
    for year in years:
        is_current = year == years[-1]
        fig.add_trace(go.Scatter(
            x=x_vals, y=cum[year].tolist(), mode="lines", name=str(year),
            line=dict(color=COLORS["current_year"] if is_current else COLORS["past_years"],
                       width=3 if is_current else 1.5),
            opacity=1.0 if is_current else 0.6,
        ))
    fig.update_layout(template="plotly_dark", paper_bgcolor=CARD_BG, plot_bgcolor=CARD_BG,
                       title=title, xaxis_title="Día del año", yaxis_title=unit,
                       height=360, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def monthly_bar_fig(monthly: pd.DataFrame, title: str, unit: str):
    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    fig = go.Figure()
    for year in sorted(monthly.columns) if not monthly.empty else []:
        fig.add_trace(go.Bar(x=[month_names[m - 1] for m in monthly.index],
                              y=monthly[year].tolist(), name=str(year)))
    fig.update_layout(template="plotly_dark", paper_bgcolor=CARD_BG, plot_bgcolor=CARD_BG,
                       title=title, barmode="group", yaxis_title=unit, height=320,
                       margin=dict(l=10, r=10, t=40, b=10))
    return fig


def goal_gauge_fig(pct, previous_year):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=pct if pct is not None else 0, number={"suffix": "%"},
        title={"text": f"Vs. {previous_year} (mismo día)"},
        gauge={
            "axis": {"range": [0, 150]},
            "bar": {"color": COLORS["current_year"]},
            "steps": [
                {"range": [0, 80], "color": "#3a2a1a"},
                {"range": [80, 100], "color": "#4a3a1a"},
                {"range": [100, 150], "color": "#1a3a2a"},
            ],
            "threshold": {"line": {"color": "white", "width": 3}, "value": 100},
        },
    ))
    fig.update_layout(template="plotly_dark", paper_bgcolor=CARD_BG, height=260,
                       margin=dict(l=20, r=20, t=50, b=10))
    return fig


def div(fig):
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False})


# ---------------------------------------------------------------------------
# Página por deporte
# ---------------------------------------------------------------------------

def sport_panel_html(df: pd.DataFrame, sport_label: str, tab_id: str, active: bool) -> str:
    years = sorted(df["date"].dt.year.unique()) if not df.empty else []
    if not years:
        return f'<div id="{tab_id}" class="tab-panel" style="display:{"block" if active else "none"};">' \
               f'<p style="color:#9AA0A6;">Sin actividades de {sport_label} todavía.</p></div>'

    current_year = years[-1]
    previous_year = years[-2] if len(years) > 1 else None

    cum_dist = cumulative_by_year(df, "distance_km")
    cum_elev = cumulative_by_year(df, "elevation_gain_m")
    cum_hours = cumulative_by_year(df, "duration_hours")

    total_dist = cum_dist[current_year].max() if current_year in cum_dist.columns else 0
    total_elev = cum_elev[current_year].max() if current_year in cum_elev.columns else 0
    total_hours = cum_hours[current_year].max() if current_year in cum_hours.columns else 0

    kpis = f"""
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px;">
      <div style="background:{CARD_BG};border-radius:12px;padding:16px;text-align:center;">
        <div style="color:#9AA0A6;font-size:13px;">Distancia YTD</div>
        <div style="font-size:26px;font-weight:700;">{total_dist:,.0f} km</div>
      </div>
      <div style="background:{CARD_BG};border-radius:12px;padding:16px;text-align:center;">
        <div style="color:#9AA0A6;font-size:13px;">Desnivel YTD</div>
        <div style="font-size:26px;font-weight:700;">{total_elev:,.0f} m</div>
      </div>
      <div style="background:{CARD_BG};border-radius:12px;padding:16px;text-align:center;">
        <div style="color:#9AA0A6;font-size:13px;">Horas YTD</div>
        <div style="font-size:26px;font-weight:700;">{total_hours:,.1f} h</div>
      </div>
    </div>"""

    top_row = f"""
    <div style="display:grid;grid-template-columns:3fr 1fr;gap:16px;">
      <div>{div(cumulative_fig(cum_dist, f"Distancia acumulada — {sport_label}", "km"))}</div>
      <div>{div(goal_gauge_fig(ytd_progress_pct(cum_dist, current_year, previous_year), previous_year)) if previous_year else "<p style='color:#9AA0A6;'>Falta un año previo para comparar.</p>"}</div>
    </div>"""

    bottom_row = f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <div>{div(monthly_bar_fig(monthly_totals(df, "elevation_gain_m"), f"Desnivel mensual — {sport_label}", "m"))}</div>
      <div>{div(monthly_bar_fig(monthly_totals(df, "duration_hours"), f"Horas mensuales — {sport_label}", "h"))}</div>
    </div>"""

    return f'<div id="{tab_id}" class="tab-panel" style="display:{"block" if active else "none"};">' \
           f'{kpis}{top_row}{bottom_row}</div>'


# ---------------------------------------------------------------------------
# Ensamblado de la página
# ---------------------------------------------------------------------------

def build_page(activities: pd.DataFrame) -> str:
    panels = [
        sport_panel_html(classify(activities, ["Ciclismo"]), "Ciclismo", "tab-ciclismo", True),
        sport_panel_html(classify(activities, ["Carrera"]), "Carrera", "tab-carrera", False),
        sport_panel_html(classify(activities, None), "General", "tab-general", False),
    ]

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Totales Anuales</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  body {{ background:{DARK_BG}; color:#e6e6e6; font-family: -apple-system, Arial, sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 22px; font-weight: 600; }}
  .tabs {{ display:flex; gap:8px; margin: 16px 0 24px; }}
  .tab-btn {{ background:{CARD_BG}; color:#e6e6e6; border:none; border-radius:8px;
              padding:10px 18px; cursor:pointer; font-size:14px; }}
  .tab-btn.active {{ background:{COLORS["current_year"]}; color:#000; font-weight:600; }}
</style>
</head>
<body>
  <h1>🚴 Totales Anuales</h1>
  <p style="color:#9AA0A6;">Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

  <div class="tabs">
    <button class="tab-btn active" onclick="showTab('tab-ciclismo', this)">🚴 Ciclismo</button>
    <button class="tab-btn" onclick="showTab('tab-carrera', this)">🏃 Carrera</button>
    <button class="tab-btn" onclick="showTab('tab-general', this)">📊 General</button>
  </div>

  {''.join(panels)}

  <script>
    function showTab(id, btn) {{
      document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
      document.getElementById(id).style.display = 'block';
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    }}
  </script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    use_garmin = os.environ.get("USE_GARMIN") == "1"
    current_year = date.today().year
    years = list(range(current_year - LOOKBACK_YEARS + 1, current_year + 1))

    if use_garmin:
        from garminconnect import Garmin
        client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
        client.login()
        activities = fetch_real_activities(client, f"{years[0]}-01-01", date.today().isoformat())
    else:
        activities = generate_mock_activities(years)

    html = build_page(activities)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Página generada: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
