#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import math
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DEFAULT_CHAT_LIMIT = 3800
HTTP_TIMEOUT_SECONDS = 12
USER_AGENT = "Mozilla/5.0 (US Market Daily English Bot)"
REPORT_TITLE_PREFIX = "US Market Daily | "

INDEX_SYMBOLS = {
    "Dow Jones": "^DJI",
    "S&P 500": "^GSPC",
    "Nasdaq Composite": "^IXIC",
    "Russell 2000": "^RUT",
    "VIX": "^VIX",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "SOXX": "SOXX",
}

SECTOR_SYMBOLS = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Financials": "XLF",
    "Industrials": "XLI",
    "Health Care": "XLV",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
}

THEME_SYMBOLS = {
    "Semiconductors": "SMH",
    "Software": "IGV",
    "Cybersecurity": "CIBR",
    "Cloud": "CLOU",
    "AI & Automation": "AIQ",
    "Small-Cap Growth": "IWO",
    "Small-Cap Value": "IWN",
    "Equal-Weight S&P 500": "RSP",
    "Large-Cap Growth": "SCHG",
    "Large-Cap Value": "VTV",
}

ASSET_SYMBOLS = {
    "DXY": "DX-Y.NYB",
    "Gold": "GC=F",
    "WTI Crude": "CL=F",
    "Brent Crude": "BZ=F",
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
}

MEGA_CAP_SYMBOLS = {
    "NVDA": "NVDA",
    "MSFT": "MSFT",
    "AAPL": "AAPL",
    "GOOGL": "GOOGL",
    "AMZN": "AMZN",
    "META": "META",
    "TSLA": "TSLA",
}

FOCUS_SYMBOLS = {
    "NVDA": "NVDA",
    "AMD": "AMD",
    "AVGO": "AVGO",
    "MRVL": "MRVL",
    "GOOGL": "GOOGL",
    "MSFT": "MSFT",
    "META": "META",
    "AMZN": "AMZN",
    "ORCL": "ORCL",
    "CRM": "CRM",
    "NOW": "NOW",
    "SNOW": "SNOW",
    "ADBE": "ADBE",
    "PANW": "PANW",
    "CRWD": "CRWD",
    "PLTR": "PLTR",
    "DDOG": "DDOG",
    "NET": "NET",
    "LITE": "LITE",
    "COHR": "COHR",
    "AAOI": "AAOI",
    "TSEM": "TSEM",
    "SIVE": "SIVE",
    "ANET": "ANET",
    "FLNC": "FLNC",
    "OKLO": "OKLO",
    "VST": "VST",
    "CEG": "CEG",
    "ETN": "ETN",
    "VRT": "VRT",
    "PWR": "PWR",
    "GEV": "GEV",
    "APLD": "APLD",
    "IREN": "IREN",
}

YIELD_SERIES = {
    "2Y": "DGS2",
    "10Y": "DGS10",
    "30Y": "DGS30",
}


@dataclass
class QuoteSnapshot:
    symbol: str
    name: str
    close: float
    previous_close: float
    high: Optional[float]
    low: Optional[float]
    history: List[Tuple[datetime, float]]

    @property
    def day_change_pct(self) -> Optional[float]:
        if (
            self.previous_close is None
            or self.close is None
            or math.isnan(self.previous_close)
            or math.isnan(self.close)
            or not self.previous_close
        ):
            return None
        return (self.close / self.previous_close - 1.0) * 100.0

    def trailing_return(self, sessions_back: int) -> Optional[float]:
        if len(self.history) <= sessions_back:
            return None
        earlier_close = self.history[-(sessions_back + 1)][1]
        if (
            earlier_close is None
            or self.close is None
            or math.isnan(earlier_close)
            or math.isnan(self.close)
            or not earlier_close
        ):
            return None
        return (self.close / earlier_close - 1.0) * 100.0


@dataclass
class ReportDataset:
    index_quotes: Dict[str, QuoteSnapshot]
    sector_quotes: Dict[str, QuoteSnapshot]
    theme_quotes: Dict[str, QuoteSnapshot]
    asset_quotes: Dict[str, QuoteSnapshot]
    mega_quotes: Dict[str, QuoteSnapshot]
    focus_quotes: Dict[str, QuoteSnapshot]
    yield_rows: Dict[str, Tuple[Optional[float], Optional[float]]]


def unavailable_snapshot(name: str, symbol: str) -> QuoteSnapshot:
    return QuoteSnapshot(symbol, name, float("nan"), float("nan"), None, None, [])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the latest English US market close report and send it to Telegram."
    )
    parser.add_argument("--env-file", default="", help="Optional path to a local env file.")
    parser.add_argument(
        "--state-file",
        default=str(Path(__file__).with_name(".state") / "last_sent.json"),
        help="Path to the local state file.",
    )
    parser.add_argument(
        "--reports-dir",
        default=str(Path(__file__).with_name("reports")),
        help="Directory for Markdown and HTML output.",
    )
    parser.add_argument("--send-test-message", action="store_true", help="Send a Telegram test message only.")
    parser.add_argument("--force", action="store_true", help="Send even if today's report was already sent.")
    return parser.parse_args()


def load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError("Env file not found: {0}".format(path))
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def merged_env(env_file_arg: str) -> Dict[str, str]:
    values = dict(os.environ)
    if env_file_arg:
        env_path = Path(env_file_arg).expanduser()
        if env_path.exists():
            values.update(load_env_file(env_path))
    return values


def load_state(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, payload: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_json(url: str) -> Dict:
    last_error: Optional[Exception] = None
    for _ in range(3):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
    raise last_error if last_error is not None else RuntimeError("Unknown fetch_json error")


def fetch_text(url: str) -> str:
    last_error: Optional[Exception] = None
    for _ in range(3):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8")
        except Exception as exc:
            last_error = exc
    raise last_error if last_error is not None else RuntimeError("Unknown fetch_text error")


def fmt_num(value: Optional[float], digits: int = 2) -> str:
    if value is None or math.isnan(value):
        return "No reliable data"
    return "{0:,.{1}f}".format(value, digits)


def fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None or math.isnan(value):
        return "No reliable data"
    sign = "+" if value >= 0 else ""
    return "{0}{1:.{2}f}%".format(sign, value, digits)


def fetch_yahoo_chart(symbol: str, range_value: str = "3mo", interval: str = "1d") -> QuoteSnapshot:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        "?range={range_value}&interval={interval}&includePrePost=false&events=div,splits"
    ).format(symbol=urllib.parse.quote(symbol, safe=""), range_value=range_value, interval=interval)
    payload = fetch_json(url)
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []

    history: List[Tuple[datetime, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        history.append((datetime.fromtimestamp(ts, tz=timezone.utc), float(close)))
    if len(history) < 2:
        raise RuntimeError("Not enough history for {0}".format(symbol))

    high = float(highs[-1]) if highs and highs[-1] is not None else None
    low = float(lows[-1]) if lows and lows[-1] is not None else None
    return QuoteSnapshot(
        symbol=symbol,
        name=meta.get("symbol", symbol),
        close=history[-1][1],
        previous_close=history[-2][1],
        high=high,
        low=low,
        history=history,
    )


def fetch_many(symbol_map: Dict[str, str]) -> Dict[str, QuoteSnapshot]:
    def load_one(item: Tuple[str, str]) -> Tuple[str, QuoteSnapshot]:
        label, symbol = item
        try:
            snapshot = fetch_yahoo_chart(symbol)
            snapshot.name = label
            return label, snapshot
        except Exception:
            return label, unavailable_snapshot(label, symbol)

    snapshots: Dict[str, QuoteSnapshot] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for label, snapshot in executor.map(load_one, symbol_map.items()):
            snapshots[label] = snapshot
    return snapshots


def fetch_fred_latest(series_id: str) -> Tuple[Optional[datetime], Optional[float], Optional[datetime], Optional[float]]:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={0}".format(series_id)
    try:
        rows = fetch_text(url).splitlines()
    except Exception:
        return None, None, None, None
    if not rows:
        return None, None, None, None
    header = rows[0].split(",")
    try:
        value_idx = header.index(series_id)
    except ValueError:
        return None, None, None, None
    entries: List[Tuple[datetime, float]] = []
    for row in rows[1:]:
        parts = row.split(",")
        if len(parts) <= value_idx:
            continue
        value = parts[value_idx]
        if not value or value == ".":
            continue
        try:
            dt = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            entries.append((dt, float(value)))
        except ValueError:
            continue
    if not entries:
        return None, None, None, None
    latest = entries[-1]
    previous = entries[-2] if len(entries) >= 2 else (None, None)
    return latest[0], latest[1], previous[0], previous[1]


def previous_trading_label(reference: datetime) -> str:
    return reference.astimezone(timezone.utc).date().isoformat()


def markdown_to_html(markdown_text: str) -> str:
    title_line = markdown_text.splitlines()[0] if markdown_text else "US Market Daily"
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "  <title>{0}</title>\n"
        "  <style>\n"
        "    body {{ margin: 0; background: #f5f3ee; color: #1d232b; font-family: 'Inter', Arial, sans-serif; line-height: 1.65; }}\n"
        "    .page {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 56px; }}\n"
        "    .card {{ background: #fffdfa; border: 1px solid #e7dece; border-radius: 18px; box-shadow: 0 12px 28px rgba(62, 47, 28, .08); overflow: hidden; }}\n"
        "    .header {{ padding: 24px 28px 12px; background: linear-gradient(135deg, #ece1d0 0%, #faf7f0 100%); border-bottom: 1px solid #e7dece; }}\n"
        "    .eyebrow {{ font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: #84633c; margin-bottom: 8px; }}\n"
        "    h1 {{ margin: 0; font-size: 28px; line-height: 1.2; color: #3d2f20; }}\n"
        "    .content {{ padding: 24px 28px 32px; font-size: 15px; white-space: pre-wrap; word-break: break-word; }}\n"
        "  </style>\n"
        "</head>\n"
        "<body><div class=\"page\"><article class=\"card\"><header class=\"header\"><div class=\"eyebrow\">US Market Close Daily</div><h1>{1}</h1></header><section class=\"content\">{2}</section></article></div></body>\n"
        "</html>\n"
    ).format(html.escape(title_line), html.escape(title_line), html.escape(markdown_text))


def split_message(text: str, limit: int = DEFAULT_CHAT_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = block
        while len(current) > limit:
            chunks.append(current[:limit])
            current = current[limit:]
    if current:
        chunks.append(current)
    return chunks


def telegram_api_request(token: str, method: str, payload: Dict[str, str]) -> Dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url="https://api.telegram.org/bot{0}/{1}".format(token, method),
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    chunks = split_message(text)
    total = len(chunks)
    for index, chunk in enumerate(chunks, 1):
        body = "[{0}/{1}]\n{2}".format(index, total, chunk) if total > 1 else chunk
        result = telegram_api_request(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": body, "disable_web_page_preview": "true"},
        )
        if not result.get("ok"):
            raise RuntimeError("Telegram send failed: {0}".format(result))


def send_failure_alert(token: str, chat_id: str, error_text: str) -> None:
    body = (
        "US Market Daily bot failed\n"
        "Time: {0}\n"
        "Reason: {1}\n"
        "Note: The main report was not generated successfully."
    ).format(datetime.now().isoformat(timespec="seconds"), error_text[:1200])
    try:
        send_telegram_message(token, chat_id, body)
    except Exception:
        pass


def safe_change_label(day_change: Optional[float]) -> str:
    if day_change is None or math.isnan(day_change):
        return "No reliable data"
    if day_change > 1:
        return "strongly higher"
    if day_change > 0.2:
        return "moderately higher"
    if day_change >= -0.2:
        return "roughly flat"
    if day_change >= -1:
        return "moderately lower"
    return "sharply lower"


def derive_market_state(index_quotes: Dict[str, QuoteSnapshot]) -> str:
    sp = index_quotes["S&P 500"].day_change_pct or 0.0
    ndx = index_quotes["Nasdaq Composite"].day_change_pct or 0.0
    russell = index_quotes["Russell 2000"].day_change_pct or 0.0
    soxx = index_quotes["SOXX"].day_change_pct or 0.0
    if soxx > 2 and ndx > sp and russell > 0:
        return "Indices firm, semis leading, risk appetite improving"
    if sp < 0 and ndx < 0 and russell < 0:
        return "Broad index selloff, risk appetite fading"
    if ndx > sp and soxx > sp:
        return "Growth leadership intact, AI hardware still in control"
    return "Selective tape, leadership still narrow"


def ranking_lines(quotes: Dict[str, QuoteSnapshot], include_periods: bool = False) -> List[str]:
    ranked = sorted(
        quotes.values(),
        key=lambda item: item.day_change_pct if item.day_change_pct is not None else -999.0,
        reverse=True,
    )
    lines = []
    for index, snap in enumerate(ranked, 1):
        line = "{0}. {1} {2}".format(index, snap.name, fmt_pct(snap.day_change_pct))
        if include_periods:
            line += " | 5d {0} | 1m {1}".format(
                fmt_pct(snap.trailing_return(5)),
                fmt_pct(snap.trailing_return(21)),
            )
        lines.append(line)
    return lines


def compute_support_resistance(snapshot: QuoteSnapshot) -> Tuple[str, str]:
    prices = [value for _, value in snapshot.history[-50:]]
    if len(prices) < 10:
        return "No reliable data", "No reliable data"
    return fmt_num(min(prices[-10:])), fmt_num(max(prices[-10:]))


def trend_label(snapshot: QuoteSnapshot) -> str:
    ret_5 = snapshot.trailing_return(5)
    ret_21 = snapshot.trailing_return(21)
    if ret_5 is None or ret_21 is None:
        return "Needs monitoring"
    if ret_5 > 3 and ret_21 > 8:
        return "Short-term overextended"
    if ret_5 > 1 and ret_21 > 0:
        return "Still strong"
    if ret_5 < -3 and ret_21 < -8:
        return "Breakdown risk"
    if ret_5 < 0 and ret_21 > 0:
        return "Pullback to support"
    if ret_5 > 0 and ret_21 < 0:
        return "Low-base recovery"
    return "Needs monitoring"


def fetch_report_dataset() -> ReportDataset:
    index_quotes = fetch_many(INDEX_SYMBOLS)
    sector_quotes = fetch_many(SECTOR_SYMBOLS)
    theme_quotes = fetch_many(THEME_SYMBOLS)
    asset_quotes = fetch_many(ASSET_SYMBOLS)
    mega_quotes = fetch_many(MEGA_CAP_SYMBOLS)
    focus_quotes = fetch_many(FOCUS_SYMBOLS)

    yield_rows: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for label, series_id in YIELD_SERIES.items():
        _, latest_val, _, prev_val = fetch_fred_latest(series_id)
        yield_rows[label] = (latest_val, prev_val)
    return ReportDataset(index_quotes, sector_quotes, theme_quotes, asset_quotes, mega_quotes, focus_quotes, yield_rows)


def build_report_messages(dataset: ReportDataset) -> Tuple[str, List[str]]:
    index_quotes = dataset.index_quotes
    sector_quotes = dataset.sector_quotes
    theme_quotes = dataset.theme_quotes
    asset_quotes = dataset.asset_quotes
    mega_quotes = dataset.mega_quotes
    focus_quotes = dataset.focus_quotes
    yield_rows = dataset.yield_rows

    report_date = previous_trading_label(index_quotes["S&P 500"].history[-1][0])
    title = "{0}{1}".format(REPORT_TITLE_PREFIX, report_date)
    sp = index_quotes["S&P 500"]
    nasdaq = index_quotes["Nasdaq Composite"]
    dow = index_quotes["Dow Jones"]
    qqq = index_quotes["QQQ"]
    iwm = index_quotes["IWM"]
    soxx = index_quotes["SOXX"]
    latest_10y = yield_rows["10Y"][0]
    market_state = derive_market_state(index_quotes)
    strongest_sector = ranking_lines(sector_quotes)[0]
    weakest_sector = ranking_lines(sector_quotes)[-1]
    mega_ranked = ranking_lines(mega_quotes)
    focus_ranked = ranking_lines(focus_quotes)

    score_index = 4 if (sp.day_change_pct or 0) > 0 and (nasdaq.day_change_pct or 0) > 0 else 2
    score_breadth = 4 if (iwm.day_change_pct or 0) > 0 and (theme_quotes["Equal-Weight S&P 500"].day_change_pct or 0) > 0 else 2
    score_ai = 5 if (soxx.day_change_pct or 0) > 1 else 3
    score_software = 4 if (theme_quotes["Software"].day_change_pct or 0) > (sp.day_change_pct or 0) else 2
    score_rates = 2 if latest_10y is not None and latest_10y < 4.6 else 4
    stage = "Trend Up" if score_index >= 4 else "High-Level Consolidation"

    msg1 = "\n".join(
        [
            title,
            "",
            "Post-close signal",
            "Stage: {0}".format(stage),
            "State: {0}".format(market_state),
            "Risk temperature: {0}".format("Medium-High" if stage == "Trend Up" else "Medium"),
            "",
            "1-minute takeaway",
            "1. S&P 500 {0} | Nasdaq {1}".format(fmt_pct(sp.day_change_pct), fmt_pct(nasdaq.day_change_pct)),
            "2. QQQ {0} | SOXX {1}".format(fmt_pct(qqq.day_change_pct), fmt_pct(soxx.day_change_pct)),
            "3. IWM {0} | breadth {1}".format(fmt_pct(iwm.day_change_pct), "improving" if score_breadth >= 4 else "mixed"),
            "4. 10Y Treasury {0}%".format(fmt_num(latest_10y)),
            "5. DXY {0} | BTC {1}".format(fmt_pct(asset_quotes["DXY"].day_change_pct), fmt_pct(asset_quotes["Bitcoin"].day_change_pct)),
            "6. Strongest sector: {0}".format(strongest_sector),
            "7. Weakest sector: {0}".format(weakest_sector),
            "8. Mega-cap leader: {0}".format(mega_ranked[0]),
            "9. Mega-cap laggard: {0}".format(mega_ranked[-1]),
            "",
            "Scorecard",
            "Index strength: {0}/5".format(score_index),
            "Breadth: {0}/5".format(score_breadth),
            "AI leadership: {0}/5".format(score_ai),
            "Software relative strength: {0}/5".format(score_software),
            "Rate pressure: {0}/5".format(score_rates),
            "",
            "Bottom line: indices {0}, breadth {1}, AI leadership {2}.".format(
                "firm" if score_index >= 4 else "mixed",
                "supportive" if score_breadth >= 4 else "still narrow",
                "still dominant" if score_ai >= 5 else "open to rotation",
            ),
        ]
    )

    msg2 = "\n".join(
        [
            "Core snapshot",
            "US equities:",
            "- Dow {0}".format(fmt_pct(dow.day_change_pct)),
            "- S&P 500 {0}".format(fmt_pct(sp.day_change_pct)),
            "- Nasdaq {0}".format(fmt_pct(nasdaq.day_change_pct)),
            "- QQQ {0} | IWM {1}".format(fmt_pct(qqq.day_change_pct), fmt_pct(iwm.day_change_pct)),
            "- SOXX {0} | IGV {1}".format(fmt_pct(soxx.day_change_pct), fmt_pct(theme_quotes["Software"].day_change_pct)),
            "",
            "Macro:",
            "- 2Y {0}% | 10Y {1}% | 30Y {2}%".format(fmt_num(yield_rows["2Y"][0]), fmt_num(yield_rows["10Y"][0]), fmt_num(yield_rows["30Y"][0])),
            "- DXY {0}".format(fmt_pct(asset_quotes["DXY"].day_change_pct)),
            "- Gold {0} | WTI {1}".format(fmt_pct(asset_quotes["Gold"].day_change_pct), fmt_pct(asset_quotes["WTI Crude"].day_change_pct)),
            "- BTC {0} | ETH {1}".format(fmt_pct(asset_quotes["Bitcoin"].day_change_pct), fmt_pct(asset_quotes["Ethereum"].day_change_pct)),
            "",
            "Sector and style leaders:",
            *["- {0}".format(line) for line in ranking_lines(sector_quotes)[:5]],
            "- Software IGV {0} | Equal-weight RSP {1}".format(
                fmt_pct(theme_quotes["Software"].day_change_pct),
                fmt_pct(theme_quotes["Equal-Weight S&P 500"].day_change_pct),
            ),
            "",
            "Top focus names:",
            *["- {0}".format(line) for line in focus_ranked[:10]],
        ]
    )

    spy = fetch_yahoo_chart("SPY")
    spy_support, spy_resistance = compute_support_resistance(spy)
    qqq_support, qqq_resistance = compute_support_resistance(qqq)
    smh_support, smh_resistance = compute_support_resistance(theme_quotes["Semiconductors"])

    msg3 = "\n".join(
        [
            "Plan and risk",
            "Bullish continuation needs:",
            "- QQQ staying stronger than SPY",
            "- SOXX staying stronger than QQQ",
            "- IWM and RSP confirming breadth",
            "- 10Y staying below 4.60%",
            "- IGV starting to catch up",
            "",
            "Risk conditions:",
            "- 10Y pushing into 4.60% to 4.70%",
            "- SOXX weakening vs QQQ",
            "- IWM rolling over, breadth fading",
            "- DXY strength pressuring risk",
            "- Crowded semi longs losing traction",
            "",
            "Key levels:",
            "- SPY support/resistance: {0} / {1}".format(spy_support, spy_resistance),
            "- QQQ support/resistance: {0} / {1}".format(qqq_support, qqq_resistance),
            "- SMH support/resistance: {0} / {1}".format(smh_support, smh_resistance),
            "",
            "Next-session watchlist:",
            "1. 10Y Treasury retest of 4.60%",
            "2. SOXX relative strength vs QQQ",
            "3. IGV catch-up confirmation",
            "4. IWM and RSP breadth follow-through",
            "5. NVDA, AVGO, MRVL, VRT, CEG structure",
            "",
            "Sources:",
            "- Yahoo Finance: https://finance.yahoo.com/",
            "- FRED: https://fred.stlouisfed.org/",
        ]
    )
    return title, [msg1, msg2, msg3]


def build_detailed_report(dataset: ReportDataset) -> Tuple[str, str]:
    index_quotes = dataset.index_quotes
    sector_quotes = dataset.sector_quotes
    theme_quotes = dataset.theme_quotes
    asset_quotes = dataset.asset_quotes
    mega_quotes = dataset.mega_quotes
    focus_quotes = dataset.focus_quotes
    yield_rows = dataset.yield_rows

    report_date = previous_trading_label(index_quotes["S&P 500"].history[-1][0])
    title = "{0}{1}".format(REPORT_TITLE_PREFIX, report_date)
    sp = index_quotes["S&P 500"]
    nasdaq = index_quotes["Nasdaq Composite"]
    dow = index_quotes["Dow Jones"]
    russell = index_quotes["Russell 2000"]
    qqq = index_quotes["QQQ"]
    iwm = index_quotes["IWM"]
    soxx = index_quotes["SOXX"]
    vix = index_quotes["VIX"]
    latest_2y = yield_rows["2Y"][0]
    latest_10y = yield_rows["10Y"][0]
    latest_30y = yield_rows["30Y"][0]
    curve_2_10 = (latest_10y - latest_2y) * 100 if latest_2y is not None and latest_10y is not None else None
    curve_10_30 = (latest_30y - latest_10y) * 100 if latest_10y is not None and latest_30y is not None else None
    market_state = derive_market_state(index_quotes)
    strongest_sector = ranking_lines(sector_quotes)[0]
    weakest_sector = ranking_lines(sector_quotes)[-1]
    strongest_theme = ranking_lines(theme_quotes)[0]
    weakest_theme = ranking_lines(theme_quotes)[-1]

    spy = fetch_yahoo_chart("SPY")
    spy_support, spy_resistance = compute_support_resistance(spy)
    qqq_support, qqq_resistance = compute_support_resistance(qqq)
    smh_support, smh_resistance = compute_support_resistance(theme_quotes["Semiconductors"])
    igv_support, igv_resistance = compute_support_resistance(theme_quotes["Software"])

    score_index = 4 if (sp.day_change_pct or 0) > 0 and (nasdaq.day_change_pct or 0) > 0 else 2
    score_breadth = 4 if (iwm.day_change_pct or 0) > 0 and (theme_quotes["Equal-Weight S&P 500"].day_change_pct or 0) > 0 else 2
    score_ai = 5 if (soxx.day_change_pct or 0) > 1 else 3
    score_software = 4 if (theme_quotes["Software"].day_change_pct or 0) > (sp.day_change_pct or 0) else 2
    score_rates = 2 if latest_10y is not None and latest_10y < 4.6 else 4
    stage = "Trend Up" if score_index >= 4 else "High-Level Consolidation"

    lines = [
        title,
        "",
        "## 1-minute summary",
        "- Index performance: S&P 500 {0}, Nasdaq {1}, IWM {2}.".format(
            fmt_pct(sp.day_change_pct), fmt_pct(nasdaq.day_change_pct), fmt_pct(iwm.day_change_pct)
        ),
        "- Market state: {0}.".format(market_state),
        "- Strongest sector: {0}.".format(strongest_sector),
        "- Weakest sector: {0}.".format(weakest_sector),
        "- Rates and dollar: 10Y {0}% | DXY {1}.".format(fmt_num(latest_10y), fmt_pct(asset_quotes["DXY"].day_change_pct)),
        "- Mega-cap dispersion: best {0}; worst {1}.".format(ranking_lines(mega_quotes)[0], ranking_lines(mega_quotes)[-1]),
        "- Session read: {0}.".format("Index strength supported by breadth" if score_breadth >= 4 else "Index strength still narrower than ideal"),
        "",
        "## Buy-side scorecard",
        "- Index strength: {0}/5".format(score_index),
        "- Breadth: {0}/5".format(score_breadth),
        "- AI leadership: {0}/5".format(score_ai),
        "- Software relative strength: {0}/5".format(score_software),
        "- Rate pressure: {0}/5".format(score_rates),
        "- Current market stage: {0}".format(stage),
        "",
        "## Full report",
        "",
        "### 0. One-line takeaway",
        "After the close, the S&P 500 moved {0}, the Nasdaq moved {1}, semis moved {2}, and the tape looked like: {3}.".format(
            safe_change_label(sp.day_change_pct), safe_change_label(nasdaq.day_change_pct), safe_change_label(soxx.day_change_pct), market_state
        ),
        "",
        "### 1. Index overview",
        "- Dow Jones: {0} | close {1} | range {2}/{3}".format(fmt_pct(dow.day_change_pct), fmt_num(dow.close), fmt_num(dow.high), fmt_num(dow.low)),
        "- S&P 500: {0} | close {1} | 5d {2} | 1m {3}".format(fmt_pct(sp.day_change_pct), fmt_num(sp.close), fmt_pct(sp.trailing_return(5)), fmt_pct(sp.trailing_return(21))),
        "- Nasdaq Composite: {0} | close {1} | 5d {2} | 1m {3}".format(fmt_pct(nasdaq.day_change_pct), fmt_num(nasdaq.close), fmt_pct(nasdaq.trailing_return(5)), fmt_pct(nasdaq.trailing_return(21))),
        "- QQQ: {0} | close {1}".format(fmt_pct(qqq.day_change_pct), fmt_num(qqq.close)),
        "- Russell 2000: {0} | close {1}".format(fmt_pct(russell.day_change_pct), fmt_num(russell.close)),
        "- SOXX: {0} | close {1}".format(fmt_pct(soxx.day_change_pct), fmt_num(soxx.close)),
        "- VIX: {0} | close {1}".format(fmt_pct(vix.day_change_pct), fmt_num(vix.close)),
        "",
        "### 2. Tape recap",
        "- S&P 500 looked {0}, Nasdaq looked {1}, which leaves tech leadership {2}.".format(
            safe_change_label(sp.day_change_pct),
            safe_change_label(nasdaq.day_change_pct),
            "stronger" if (nasdaq.day_change_pct or 0) > (sp.day_change_pct or 0) else "less decisive",
        ),
        "- IWM {0} and RSP {1} suggest breadth is {2}.".format(
            fmt_pct(iwm.day_change_pct),
            fmt_pct(theme_quotes["Equal-Weight S&P 500"].day_change_pct),
            "broadening" if score_breadth >= 4 else "still mixed",
        ),
        "- SMH {0} versus IGV {1} says the market still leans {2}.".format(
            fmt_pct(theme_quotes["Semiconductors"].day_change_pct),
            fmt_pct(theme_quotes["Software"].day_change_pct),
            "toward AI hardware" if score_ai >= 5 else "toward broader growth rotation",
        ),
        "",
        "### 3. Macro backdrop",
        "- 2Y Treasury: {0}% | 10Y: {1}% | 30Y: {2}%".format(fmt_num(latest_2y), fmt_num(latest_10y), fmt_num(latest_30y)),
        "- Curve: 2Y-10Y {0}bp | 10Y-30Y {1}bp".format(fmt_num(curve_2_10), fmt_num(curve_10_30)),
        "- DXY {0} | Gold {1} | WTI {2}".format(fmt_pct(asset_quotes["DXY"].day_change_pct), fmt_pct(asset_quotes["Gold"].day_change_pct), fmt_pct(asset_quotes["WTI Crude"].day_change_pct)),
        "- Bitcoin {0} | Ethereum {1}".format(fmt_pct(asset_quotes["Bitcoin"].day_change_pct), fmt_pct(asset_quotes["Ethereum"].day_change_pct)),
        "- FedWatch / scheduled macro data: No reliable data.",
        "",
        "### 4. Sector performance",
        "- Best sector: {0}".format(strongest_sector),
        "- Weakest sector: {0}".format(weakest_sector),
        "- Short-term leaderboard:",
        *["  - {0}".format(line) for line in ranking_lines(sector_quotes, include_periods=True)[:5]],
        "",
        "### 5. Theme and style",
        "- Best theme: {0}".format(strongest_theme),
        "- Weakest theme: {0}".format(weakest_theme),
        "- Software IGV {0} | Semis SMH {1} | Equal-weight RSP {2}".format(
            fmt_pct(theme_quotes["Software"].day_change_pct),
            fmt_pct(theme_quotes["Semiconductors"].day_change_pct),
            fmt_pct(theme_quotes["Equal-Weight S&P 500"].day_change_pct),
        ),
        "",
        "### 6. Breadth and participation",
        "- IWM {0} | RSP {1}".format(fmt_pct(iwm.day_change_pct), fmt_pct(theme_quotes["Equal-Weight S&P 500"].day_change_pct)),
        "- Breadth verdict: {0}.".format("expanding" if score_breadth >= 4 else "still concentrated"),
        "- 20/50/100/200dma participation: No reliable data.",
        "- Advance/decline and highs/lows: No reliable data.",
        "",
        "### 7. Technical view",
        "- SPY support/resistance: {0} / {1}".format(spy_support, spy_resistance),
        "- QQQ support/resistance: {0} / {1}".format(qqq_support, qqq_resistance),
        "- SMH support/resistance: {0} / {1}".format(smh_support, smh_resistance),
        "- IGV support/resistance: {0} / {1}".format(igv_support, igv_resistance),
        "- Technical conclusion: if QQQ stays stronger than SPY and SMH stays stronger than QQQ, trend continuation remains the base case.",
        "",
        "### 8. Key stocks and movers",
        "- Mega-cap ranking:",
        *["  - {0}".format(line) for line in ranking_lines(mega_quotes)],
        "- Top focus names:",
        *["  - {0}".format(line) for line in ranking_lines(focus_quotes)[:10]],
        "- Earnings / ratings / company news: No reliable data.",
        "",
        "### 9. Earnings calendar",
        "- Key reports just released: No reliable data.",
        "- Important reports over the next 1 to 3 sessions: No reliable data.",
        "",
        "### 10. Flows and street view",
        "- ETF flows / options activity / block trades: No reliable data.",
        "- Primary sources to monitor: Yahoo Finance, Nasdaq, company IR pages, SEC filings.",
        "",
        "### 11. Rotation call",
        "- Current regime looks closest to: {0}.".format(stage if score_ai >= 5 else "Sector Rotation"),
        "- Capital is flowing into: {0}.".format("Semis, AI power chain, core growth" if score_ai >= 5 else "Software and equal-weight catch-up"),
        "- Capital is leaving: {0}.".format("Defensives" if (sector_quotes["Utilities"].day_change_pct or 0) < 0 else "weaker consumer and real estate groups"),
        "",
        "### 12. Focus list snapshot",
        *[
            "- {0}: {1} | 5d {2} | 1m {3}".format(name, trend_label(snap), fmt_pct(snap.trailing_return(5)), fmt_pct(snap.trailing_return(21)))
            for name, snap in list(focus_quotes.items())[:15]
        ],
        "",
        "### 13. Next-session plan",
        "- Macro: watch whether the 10Y Treasury retests 4.60% to 4.70% and whether DXY keeps firming.",
        "- Indices: watch whether QQQ stays stronger than SPY and whether IWM/RSP confirm breadth.",
        "- Themes: watch whether SMH keeps leading and whether IGV starts to catch up.",
        "- Stocks: watch NVDA, AVGO, MRVL, VRT, CEG, MSFT, ORCL, CRM, NOW, CRWD.",
        "- If markets keep rising: look for SOXX and QQQ to confirm together with volume.",
        "- If markets pull back: first watch whether QQQ {0} and SPY {1} hold.".format(qqq_support, spy_support),
        "",
        "### 14. Risk checklist",
        "- Treasury yields continue to move higher.",
        "- Semis are crowded and increasingly prone to good-news fatigue.",
        "- Index strength may outpace internal breadth.",
        "- Software or broader growth earnings may disappoint.",
        "- A stronger dollar may pressure risk assets.",
        "",
        "### 15. Final call",
        "- Market conclusion: {0}.".format(market_state),
        "- Current stage: {0}.".format(stage),
        "- Trading bias: avoid chasing; wait for breadth and rates to confirm before adding risk.",
        "- Five key signals:",
        "  1. 10Y Treasury above or below 4.60%",
        "  2. SOXX relative strength vs QQQ",
        "  3. IGV catch-up confirmation",
        "  4. IWM and RSP broadening together",
        "  5. NVDA / AVGO / MRVL holding structure",
        "",
        "## Sources",
        "- Yahoo Finance: https://finance.yahoo.com/",
        "- FRED: https://fred.stlouisfed.org/",
    ]
    return title, "\n".join(lines).strip() + "\n"


def build_report_outputs() -> Tuple[str, List[str], str]:
    dataset = fetch_report_dataset()
    title, messages = build_report_messages(dataset)
    _, detailed_report = build_detailed_report(dataset)
    return title, messages, detailed_report


def save_report_files(report_text: str, reports_dir: Path, report_date: str) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = reports_dir / "{0}.md".format(report_date)
    html_path = reports_dir / "{0}.html".format(report_date)
    latest_md = reports_dir / "latest.md"
    latest_html = reports_dir / "latest.html"
    markdown_path.write_text(report_text, encoding="utf-8")
    html_path.write_text(markdown_to_html(report_text), encoding="utf-8")
    latest_md.write_text(report_text, encoding="utf-8")
    latest_html.write_text(markdown_to_html(report_text), encoding="utf-8")
    return markdown_path, html_path


def main() -> int:
    args = parse_args()
    env = merged_env(args.env_file)
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")

    try:
        if args.send_test_message:
            send_telegram_message(token, chat_id, "Telegram test passed\nTime: {0}".format(datetime.now().isoformat(timespec="seconds")))
            return 0

        title, messages, detailed_report_text = build_report_outputs()
        report_date = title.replace(REPORT_TITLE_PREFIX, "", 1)
        reports_dir = Path(args.reports_dir).expanduser()
        markdown_path, html_path = save_report_files(detailed_report_text, reports_dir, report_date)

        state_path = Path(args.state_file).expanduser()
        state = load_state(state_path)
        report_id = hashlib.sha256(detailed_report_text.encode("utf-8")).hexdigest()
        if not args.force and state.get("last_sent_report_date") == report_date:
            print("Latest report for {0} already sent.".format(report_date))
            print("Markdown saved to {0}".format(markdown_path))
            print("HTML saved to {0}".format(html_path))
            return 0

        for message in messages:
            send_telegram_message(token, chat_id, message)
        save_state(
            state_path,
            {
                "last_sent_report_date": report_date,
                "last_sent_report_id": report_id,
                "last_sent_at": datetime.now().isoformat(timespec="seconds"),
                "report_markdown_path": str(markdown_path),
                "report_html_path": str(html_path),
            },
        )
        print("Sent report for {0}".format(report_date))
        print("Markdown saved to {0}".format(markdown_path))
        print("HTML saved to {0}".format(html_path))
        return 0
    except Exception as exc:
        send_failure_alert(token, chat_id, repr(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
