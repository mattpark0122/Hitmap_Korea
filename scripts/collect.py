#!/usr/bin/env python
"""코스피·코스닥 전 종목(종목명·코드·종가·등락률·시가총액·업종)과 지수를 수집한다.

데이터 소스 (auto 모드 우선순위)
  1. pykrx  : KRX 정보데이터시스템. 2025년 말부터 KRX 로그인이 필요해서
              환경변수(또는 프로젝트 루트 .env) KRX_ID / KRX_PW 가 있어야 동작한다.
              과거 날짜(--date) 수집도 이 경로만 가능하다.
  2. naver  : 네이버 금융 모바일 API. 로그인 불필요, 최신 거래일만 제공한다.

업종(섹터) 우선순위
  KRX 업종분류(pykrx) → 네이버 업종 → 우선주는 보통주 업종 → reference 매핑 → '기타'

저장
  data/{kospi|kosdaq}/YYYYMMDD.json  (이미 있으면 덮어쓰지 않음, --force 로만 덮어씀)
  data/{kospi|kosdaq}/latest.json    (가장 최근 날짜 파일의 사본)
  data/{kospi|kosdaq}/dates.json     (보유 날짜 목록, 페이지의 날짜 선택용)
  data/{kospi|kosdaq}/news/YYYYMMDD.json (섹터별 관련 기사, scripts/news.py, --no-news 로 생략)

사용 예
  python scripts/collect.py                    # 두 시장, 직전(또는 오늘) 거래일
  python scripts/collect.py --market kosdaq
  python scripts/collect.py --date 20261105    # 과거 날짜 (pykrx + KRX 로그인 필요)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sector_fallback  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
CLOSE_READY = (15, 40)  # 이 시각(KST) 이후에만 '오늘'을 종가 확정일로 본다

MARKETS = {
    "kospi": {"code": "KOSPI", "name": "코스피", "krx_index": "1001"},
    "kosdaq": {"code": "KOSDAQ", "name": "코스닥", "krx_index": "2001"},
}


class SourceError(Exception):
    pass


def log(*a):
    print(*a, flush=True)


def load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def now_kst() -> datetime:
    return datetime.now(KST)


def num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def r2(v, nd=2):
    return None if v is None else round(float(v), nd)


# --------------------------------------------------------------------------- #
# 네이버 금융
# --------------------------------------------------------------------------- #
NAVER = "https://m.stock.naver.com/api"
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (hitmap-korea collector)"


def nget(path: str, params=None, retries=3):
    last = None
    for i in range(retries):
        try:
            r = _session.get(NAVER + path, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + i)
    raise SourceError(f"네이버 요청 실패 {path}: {last}")


def naver_trade_date(mkt: str) -> tuple[str, dict]:
    """네이버 기준 최신 '확정' 거래일과 그날 지수 정보를 돌려준다."""
    code = MARKETS[mkt]["code"]
    basic = nget(f"/index/{code}/basic")
    if basic.get("marketStatus") != "CLOSE":
        raise SourceError(
            f"{code} 장이 아직 마감되지 않았습니다(status={basic.get('marketStatus')}). "
            "네이버는 최신 시세만 주므로 장 마감(15:30) 이후에 다시 실행하세요."
        )
    date = basic["localTradedAt"][:10].replace("-", "")
    hist = nget(f"/index/{code}/price", {"pageSize": 5, "page": 1})
    row = next((h for h in hist if h["localTradedAt"].replace("-", "") == date), None)
    src = row or basic
    index = {
        "name": MARKETS[mkt]["name"],
        "close": r2(num(src["closePrice"])),
        "change": r2(num(src["compareToPreviousClosePrice"])),
        "change_pct": r2(num(src["fluctuationsRatio"])),
    }
    return date, index


def naver_stocks(mkt: str) -> list[dict]:
    code = MARKETS[mkt]["code"]
    out, page, size = [], 1, 100
    while True:
        d = nget(f"/stocks/marketValue/{code}", {"page": page, "pageSize": size})
        rows = d.get("stocks") or []
        for s in rows:
            if s.get("stockEndType") != "stock":
                continue
            cap = num(s.get("marketValue"))  # 억원
            if not cap:
                continue
            out.append({
                "code": s["itemCode"],
                "name": s["stockName"],
                "close": num(s.get("closePrice")),
                "chg": r2(num(s.get("fluctuationsRatio"))),
                "cap": cap,
                "sector": None,
            })
        total = d.get("totalCount") or 0
        if len(rows) < size or page * size >= total:
            break
        page += 1
        time.sleep(0.1)
    if not out:
        raise SourceError(f"네이버 {code} 종목 목록이 비어 있습니다.")
    return out


_naver_sector_cache: dict[str, str] | None = None


def naver_sector_map() -> dict[str, str]:
    """종목코드 → 네이버 업종명 (코스피·코스닥 공통, 실행당 1회만 조회)."""
    global _naver_sector_cache
    if _naver_sector_cache is not None:
        return _naver_sector_cache
    groups, page = [], 1
    while True:
        d = nget("/stocks/industry", {"page": page, "pageSize": 50})
        groups += d.get("groups") or []
        if len(groups) >= (d.get("totalCount") or 0) or not d.get("groups"):
            break
        page += 1
    m: dict[str, str] = {}
    for g in groups:
        page = 1
        while True:
            d = nget(f"/stocks/industry/{g['no']}", {"page": page, "pageSize": 100})
            rows = d.get("stocks") or []
            for s in rows:
                m.setdefault(s["itemCode"], g["name"])
            if len(rows) < 100:
                break
            page += 1
        time.sleep(0.05)
    log(f"  네이버 업종 {len(groups)}개, {len(m)}종목 매핑")
    _naver_sector_cache = m
    return m


def collect_naver(mkt: str, req_date: str) -> dict:
    date, index = naver_trade_date(mkt)
    if req_date < date:
        raise SourceError(
            f"네이버는 최신 거래일({date})만 제공합니다. 과거 날짜 {req_date}는 "
            "pykrx(KRX_ID/KRX_PW 설정)로 수집하세요."
        )
    return {"date": date, "index": index, "loader": lambda: naver_stocks(mkt),
            "price_source": "naver"}


# --------------------------------------------------------------------------- #
# pykrx (KRX 로그인 필요)
# --------------------------------------------------------------------------- #
def krx_credentials() -> bool:
    return bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))


def collect_pykrx(mkt: str, req_date: str) -> dict:
    if not krx_credentials():
        raise SourceError("KRX_ID / KRX_PW 가 설정되지 않아 pykrx를 쓸 수 없습니다.")
    from pykrx import stock  # 환경변수를 읽은 뒤에 import

    m = MARKETS[mkt]
    # 오늘이고 아직 장 마감 전이면 어제부터 직전 거래일을 찾는다
    now = now_kst()
    probe = req_date
    if req_date == now.strftime("%Y%m%d") and (now.hour, now.minute) < CLOSE_READY:
        probe = (now - timedelta(days=1)).strftime("%Y%m%d")
    try:
        date = stock.get_nearest_business_day_in_a_week(probe, prev=True)
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"pykrx 거래일 조회 실패: {e}") from e

    start = (datetime.strptime(date, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d")
    idx = stock.get_index_ohlcv(start, date, m["krx_index"])
    if idx is None or idx.empty or idx.index[-1].strftime("%Y%m%d") != date:
        raise SourceError("pykrx 지수 조회 실패")
    close = float(idx["종가"].iloc[-1])
    prev = float(idx["종가"].iloc[-2]) if len(idx) > 1 else None
    index = {
        "name": m["name"],
        "close": r2(close),
        "change": r2(close - prev) if prev else None,
        "change_pct": r2((close / prev - 1) * 100) if prev else None,
    }

    def loader():
        rows = []
        try:
            df = stock.get_market_sector_classifications(date, m["code"])
        except Exception as e:  # noqa: BLE001
            log(f"  pykrx 업종분류 실패({e}), 시세/시총만 따로 조회")
            df = None
        if df is not None and not df.empty:
            for code, r in df.iterrows():
                cap = float(r["시가총액"]) / 1e8
                if cap <= 0:
                    continue
                rows.append({"code": str(code), "name": r["종목명"], "close": float(r["종가"]),
                             "chg": r2(r["등락률"]), "cap": cap,
                             "sector": (str(r["업종명"]).strip() or None), "_src": "krx"})
            return rows
        ohlcv = stock.get_market_ohlcv(date, market=m["code"])
        cap = stock.get_market_cap(date, market=m["code"])
        if ohlcv.empty or cap.empty:
            raise SourceError("pykrx 시세/시총 조회 결과가 비어 있습니다.")
        for code, r in ohlcv.iterrows():
            if code not in cap.index:
                continue
            c = float(cap.loc[code, "시가총액"]) / 1e8
            if c <= 0:
                continue
            rows.append({"code": str(code), "name": stock.get_market_ticker_name(code),
                         "close": float(r["종가"]), "chg": r2(r["등락률"]), "cap": c, "sector": None})
        return rows

    return {"date": date, "index": index, "loader": loader, "price_source": "pykrx"}


# --------------------------------------------------------------------------- #
# 업종 채우기 · 저장
# --------------------------------------------------------------------------- #
def fill_sectors(stocks: list[dict]) -> dict:
    counts: dict[str, int] = {}

    def mark(s, src):
        s["_src"] = src
        counts[src] = counts.get(src, 0) + 1

    for s in stocks:
        if s.get("sector") and s.get("_src") == "krx":
            mark(s, "krx")
    missing = [s for s in stocks if not s.get("sector")]
    if missing:
        try:
            nmap = naver_sector_map()
        except SourceError as e:
            log(f"  네이버 업종 조회 실패: {e}")
            nmap = {}
        for s in missing:
            if s["code"] in nmap:
                s["sector"] = nmap[s["code"]]
                mark(s, "naver")
    by_code = {s["code"]: s for s in stocks}
    for s in stocks:
        if s.get("sector"):
            continue
        parent = by_code.get(s["code"][:5] + "0")  # 우선주 → 보통주
        if s["code"][-1] != "0" and parent and parent.get("sector"):
            s["sector"] = parent["sector"]
            mark(s, "preferred-parent")
            continue
        ref = sector_fallback.lookup(s["name"])
        if ref:
            s["sector"] = ref
            mark(s, "reference")
        else:
            s["sector"] = "기타"
            mark(s, "unclassified")
    for s in stocks:
        s.pop("_src", None)
    return counts


def summarize(stocks: list[dict]) -> dict:
    known = [s for s in stocks if s["chg"] is not None]
    cap = sum(s["cap"] for s in known)
    return {
        "count": len(stocks),
        "up": sum(1 for s in known if s["chg"] > 0),
        "down": sum(1 for s in known if s["chg"] < 0),
        "flat": sum(1 for s in known if s["chg"] == 0),
        "cap_weighted_chg": r2(sum(s["cap"] * s["chg"] for s in known) / cap) if cap else None,
        "total_cap": round(sum(s["cap"] for s in stocks)),
    }


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def refresh_index_files(mkt: str):
    """dates.json 갱신, latest.json 을 가장 최근 날짜 파일과 맞춘다."""
    d = DATA / mkt
    dates = sorted((p.stem for p in d.glob("[0-9]" * 8 + ".json")), reverse=True)
    write_json(d / "dates.json", {"market": MARKETS[mkt]["code"], "dates": dates})
    if not dates:
        return
    newest = (d / f"{dates[0]}.json").read_text(encoding="utf-8")
    latest = d / "latest.json"
    if not latest.exists() or latest.read_text(encoding="utf-8") != newest:
        tmp = latest.with_suffix(".tmp")
        tmp.write_text(newest, encoding="utf-8")
        os.replace(tmp, latest)
        log(f"  latest.json → {dates[0]}")


def collect_market(mkt: str, req_date: str, source: str, force: bool, news: bool = True) -> bool:
    ok = _collect_market(mkt, req_date, source, force)
    if ok and news:
        import news as news_mod  # 지연 import (news.py 가 이 모듈을 import 함)
        date = _last_date.get(mkt)
        try:
            news_mod.collect_news(mkt, date, force)
        except Exception as e:  # noqa: BLE001  뉴스 실패가 시세 수집을 실패로 만들지 않게
            log(f"  섹터 뉴스 수집 실패: {e}")
    return ok


_last_date: dict[str, str] = {}


def _collect_market(mkt: str, req_date: str, source: str, force: bool) -> bool:
    log(f"[{MARKETS[mkt]['code']}] 요청일 {req_date}, 소스 {source}")
    tries = {"auto": ["pykrx", "naver"], "pykrx": ["pykrx"], "naver": ["naver"]}[source]
    if source == "auto" and not krx_credentials():
        log("  KRX_ID/KRX_PW 미설정 → pykrx 건너뛰고 네이버 사용")
        tries = ["naver"]
    plan, errors = None, []
    for t in tries:
        try:
            plan = (collect_pykrx if t == "pykrx" else collect_naver)(mkt, req_date)
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{t}: {e}")
            log(f"  {t} 실패: {e}")
    if plan is None:
        log("  수집 실패:\n   - " + "\n   - ".join(errors))
        return False

    date = plan["date"]
    _last_date[mkt] = date
    if date != req_date:
        log(f"  {req_date}는 거래일이 아니거나 미확정 → 직전 거래일 {date} 사용")
    path = DATA / mkt / f"{date}.json"
    if path.exists() and not force:
        log(f"  {path.relative_to(ROOT)} 이미 있음 → 덮어쓰지 않음 (--force 로 강제)")
        refresh_index_files(mkt)
        return True

    stocks = plan["loader"]()
    sector_counts = fill_sectors(stocks)
    for s in stocks:
        s["cap"] = round(s["cap"])
        s["close"] = None if s["close"] is None else round(s["close"])
    stocks.sort(key=lambda s: -s["cap"])
    out = {
        "market": MARKETS[mkt]["code"],
        "market_name": MARKETS[mkt]["name"],
        "date": date,
        "generated_at": now_kst().isoformat(timespec="seconds"),
        "source": {"prices": plan["price_source"], "sectors": sector_counts},
        "units": {"close": "원", "chg": "%", "cap": "억원"},
        "index": plan["index"],
        "summary": summarize(stocks),
        "stocks": stocks,
    }
    write_json(path, out)
    log(f"  저장 {path.relative_to(ROOT)}: {len(stocks)}종목, 업종 출처 {sector_counts}")
    refresh_index_files(mkt)
    return True


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["all", "kospi", "kosdaq"], default="all")
    ap.add_argument("--date", help="YYYYMMDD (기본: 오늘 KST, 휴장/장중이면 직전 거래일)")
    ap.add_argument("--source", choices=["auto", "pykrx", "naver"], default="auto")
    ap.add_argument("--force", action="store_true", help="같은 날짜 파일이 있어도 다시 수집해 덮어씀")
    ap.add_argument("--no-news", action="store_true", help="섹터 뉴스(data/{시장}/news/) 수집 생략")
    a = ap.parse_args()
    req = a.date or now_kst().strftime("%Y%m%d")
    mkts = ["kospi", "kosdaq"] if a.market == "all" else [a.market]
    ok = all([collect_market(m, req, a.source, a.force, not a.no_news) for m in mkts])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
