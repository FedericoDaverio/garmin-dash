"""
build_training_status.py
-------------------------
Genera una página estática (training_status.html) con el estado de
entrenamiento: VO2max, Training Load (agudo/crónico + ACWR), Training
Readiness, FTP, y el status más reciente.

Pensado para el mismo flujo que build_yearly_totals.py: se corre vía
GitHub Actions, no requiere servidor, y el HTML resultante se publica
en GitHub Pages.

Uso:
    python build_training_status.py                 # con mock data
    USE_GARMIN=1 python build_training_status.py     # con datos reales
    (credenciales via GARMIN_EMAIL / GARMIN_PASSWORD en el entorno)

Nota sobre FTP:
    La API no oficial de Garmin no expone de forma confiable un
    histórico de FTP por fecha. Siguiendo el mismo principio que ya usas
    en el dashboard Streamlit (FTP nunca hardcodeado), este script lee
    el histórico desde data/ftp_history.csv (columnas: date,ftp_watts),
    que tú actualizas a mano cada vez que haces un test de FTP. Si el
    archivo no existe, esa sección simplemente se omite.
"""

import csv
import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go

DARK_BG = "#0e1117"
CARD_BG = "#161b22"
COLORS = {
    "vo2max_run": "#FFA630",
    "vo2max_bike": "#4D96FF",
    "load_acute": "#FF6B6B",
    "load_chronic": "#9AA0A6",
    "readiness": "#6BCB77",
    "ftp": "#FFD93D",
}

OUTPUT_PATH = "docs/training_status.html"
FTP_CSV_PATH = os.path.join("data", "ftp_history.csv")
LOOKBACK_DAYS = 120


# ---------------------------------------------------------------------------
# Fuente de datos: Garmin real
# ---------------------------------------------------------------------------

def safe_path(d, *keys, default=None):
    """Navega un dict anidado sin lanzar KeyError si algo no existe.
    Los nombres de campos de la API no oficial de Garmin pueden cambiar
    entre versiones de la librería — por eso todo aquí es defensivo."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def fetch_vo2max_series(client, days: int) -> pd.DataFrame:
    rows = []
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            metrics = client.get_max_metrics(d)
        except Exception:
            continue
        if not metrics:
            continue
        entry = metrics[0] if isinstance(metrics, list) else metrics
        run_v = safe_path(entry, "generic", "vo2MaxPreciseValue") or safe_path(entry, "generic", "vo2MaxValue")
        bike_v = safe_path(entry, "cycling", "vo2MaxValue")
        if run_v or bike_v:
            rows.append({"date": d, "vo2max_run": run_v, "vo2max_bike": bike_v})
    return pd.DataFrame(rows)


def fetch_training_load_series(client, days: int) -> tuple[pd.DataFrame, dict]:
    rows = []
    latest_status = {}
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            status = client.get_training_status(d)
        except Exception:
            continue
        if not status:
            continue
        acute = safe_path(status, "mostRecentTrainingLoadBalance", "acuteTrainingLoad")
        chronic = safe_path(status, "mostRecentTrainingLoadBalance", "chronicTrainingLoad")
        phrase = safe_path(status, "mostRecentTrainingStatus", "trainingStatusFeedbackPhrase")
        if acute is not None or chronic is not None:
            rows.append({"date": d, "acute": acute, "chronic": chronic})
        if i == 0 and phrase:
            latest_status = {"date": d, "phrase": phrase}
    return pd.DataFrame(rows), latest_status


def fetch_readiness_series(client, days: int) -> pd.DataFrame:
    rows = []
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            r = client.get_training_readiness(d)
        except Exception:
            continue
        if not r:
            continue
        entry = r[0] if isinstance(r, list) else r
        score = safe_path(entry, "score") or entry.get("trainingReadinessScore") if isinstance(entry, dict) else None
        if score is not None:
            rows.append({"date": d, "readiness": score})
    return pd.DataFrame(rows)


def load_ftp_history() -> pd.DataFrame:
    if not os.path.exists(FTP_CSV_PATH):
        return pd.DataFrame()
    df = pd.read_csv(FTP_CSV_PATH)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"]).sort_values("date")


# ---------------------------------------------------------------------------
# Mock data (para probar la página sin credenciales)
# ---------------------------------------------------------------------------

def generate_mock_data(days: int):
    rng = np.random.default_rng(7)
    dates = pd.date_range(end=date.today(), periods=days)

    vo2 = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "vo2max_run": 48 + np.cumsum(rng.normal(0, 0.05, days)),
        "vo2max_bike": 50 + np.cumsum(rng.normal(0, 0.05, days)),
    })

    base_load = 60 + 20 * np.sin(np.linspace(0, 6, days))
    acute = base_load + rng.normal(0, 8, days)
    chronic = pd.Series(base_load).rolling(28, min_periods=1).mean()
    load = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "acute": acute, "chronic": chronic})

    readiness = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "readiness": np.clip(70 + rng.normal(0, 10, days), 0, 100),
    })

    ftp_dates = pd.date_range(end=date.today(), periods=6, freq="30D")
    ftp = pd.DataFrame({"date": ftp_dates, "ftp_watts": [210, 215, 218, 222, 228, 231]})

    latest_status = {"date": dates[-1].strftime("%Y-%m-%d"), "phrase": "PRODUCTIVE"}
    return vo2, load, readiness, ftp, latest_status


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def line_fig(df, x, series: dict, title, unit):
    fig = go.Figure()
    for col, (label, color) in series.items():
        if col in df.columns and df[col].notna().any():
            fig.add_trace(go.Scatter(x=df[x], y=df[col], mode="lines", name=label,
                                      line=dict(color=color, width=2.5)))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=CARD_BG, plot_bgcolor=CARD_BG,
        title=title, yaxis_title=unit, height=340, margin=dict(l=10, r=10, t=40, b=10),
    )
    return fig


def readiness_gauge(latest_value):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=0 if latest_value is None else latest_value,
        title={"text": "Training Readiness (hoy)"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": COLORS["readiness"]},
            "steps": [
                {"range": [0, 25], "color": "#3a1a1a"},
                {"range": [25, 50], "color": "#3a2a1a"},
                {"range": [50, 75], "color": "#2a3a1a"},
                {"range": [75, 100], "color": "#1a3a2a"},
            ],
        },
    ))
    fig.update_layout(template="plotly_dark", paper_bgcolor=CARD_BG, height=300,
                       margin=dict(l=20, r=20, t=50, b=10))
    return fig


def status_badge_html(latest_status: dict) -> str:
    phrase = latest_status.get("phrase", "SIN DATOS")
    d = latest_status.get("date", "")
    colors = {
        "PRODUCTIVE": "#6BCB77", "PEAKING": "#4D96FF", "MAINTAINING": "#9AA0A6",
        "OVERREACHING": "#FF6B6B", "RECOVERY": "#FFD93D", "DETRAINING": "#FF924C",
        "UNPRODUCTIVE": "#FF6B6B",
    }
    color = colors.get(phrase.upper().replace(" ", "_"), "#9AA0A6")
    return f"""
    <div style="background:{CARD_BG};border-radius:12px;padding:24px;text-align:center;">
      <div style="color:#9AA0A6;font-size:14px;">Estado de entrenamiento ({d})</div>
      <div style="color:{color};font-size:32px;font-weight:700;margin-top:6px;">{phrase}</div>
    </div>
    """


# ---------------------------------------------------------------------------
# Ensamblado de la página
# ---------------------------------------------------------------------------

def build_page(vo2_df, load_df, readiness_df, ftp_df, latest_status):
    fig_vo2 = line_fig(
        vo2_df, "date",
        {"vo2max_run": ("VO2max corriendo", COLORS["vo2max_run"]),
         "vo2max_bike": ("VO2max ciclismo", COLORS["vo2max_bike"])},
        "VO2max", "ml/kg/min",
    )
    fig_load = line_fig(
        load_df, "date",
        {"acute": ("Carga aguda (7d)", COLORS["load_acute"]),
         "chronic": ("Carga crónica (28d)", COLORS["load_chronic"])},
        "Training Load", "UA",
    )
    latest_readiness = readiness_df["readiness"].iloc[-1] if not readiness_df.empty else None
    fig_readiness_gauge = readiness_gauge(latest_readiness)
    fig_readiness_trend = line_fig(
        readiness_df, "date", {"readiness": ("Training Readiness", COLORS["readiness"])},
        "Training Readiness — tendencia", "score",
    )
    fig_ftp = line_fig(
        ftp_df, "date", {"ftp_watts": ("FTP", COLORS["ftp"])}, "FTP", "watts",
    ) if not ftp_df.empty else None

    def div(fig):
        return fig.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False})

    sections = [status_badge_html(latest_status)]
    sections.append(div(fig_vo2))
    sections.append(div(fig_load))

    cols = f"""<div style="display:grid;grid-template-columns:1fr 2fr;gap:16px;">
        <div>{div(fig_readiness_gauge)}</div>
        <div>{div(fig_readiness_trend)}</div>
    </div>"""
    sections.append(cols)

    if fig_ftp is not None:
        sections.append(div(fig_ftp))
    else:
        sections.append(
            '<p style="color:#9AA0A6;text-align:center;">Sin histórico de FTP '
            '(agrega data/ftp_history.csv con columnas date,ftp_watts).</p>'
        )

    body = "\n<hr style='border-color:#222;margin:24px 0;'>\n".join(sections)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Estado de Entrenamiento</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  body {{ background:{DARK_BG}; color:#e6e6e6; font-family: -apple-system, Arial, sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 22px; font-weight: 600; }}
</style>
</head>
<body>
  <h1>📈 Estado de Entrenamiento</h1>
  <p style="color:#9AA0A6;">Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  {body}
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    use_garmin = os.environ.get("USE_GARMIN") == "1"

    if use_garmin:
        from garminconnect import Garmin
        email = os.environ["GARMIN_EMAIL"]
        password = os.environ["GARMIN_PASSWORD"]
        client = Garmin(email, password)
        client.login()
        vo2_df = fetch_vo2max_series(client, LOOKBACK_DAYS)
        load_df, latest_status = fetch_training_load_series(client, LOOKBACK_DAYS)
        readiness_df = fetch_readiness_series(client, LOOKBACK_DAYS)
        ftp_df = load_ftp_history()
    else:
        vo2_df, load_df, readiness_df, mock_ftp, latest_status = generate_mock_data(LOOKBACK_DAYS)
        ftp_df = load_ftp_history()
        if ftp_df.empty:
            ftp_df = mock_ftp

    html = build_page(vo2_df, load_df, readiness_df, ftp_df, latest_status)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Página generada: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
