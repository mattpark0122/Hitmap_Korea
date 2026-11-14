#!/usr/bin/env python
"""섹터별 관련 기사 수집 (네이버 종목 뉴스).

섹터마다 시총 상위 3종목 + 등락률 절댓값 상위 2종목의 종목 뉴스를 모아
거래일 전후 기사만 남기고, 중복·시황/공시 모음 기사를 걸러 섹터당 최대 5건을 저장한다.
저작권 때문에 본문은 저장하지 않고 제목·언론사·시각·원문 링크만 남긴다.

저장: data/{kospi|kosdaq}/news/YYYYMMDD.json (이미 있으면 덮어쓰지 않음, --force 로만)

사용 예
  python scripts/news.py                 # 두 시장, 각 시장의 latest 날짜
  python scripts/news.py --market kosdaq --date 20261106 --force
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import DATA, KST, MARKETS, ROOT, SourceError, log, nget, now_kst, write_json  # noqa: E402

PER_SECTOR = 5
TOP_BY_CAP = 3
TOP_BY_MOVE = 2
MAX_PAGES = 5
PAGE_SIZE = 20

# 섹터 설명력이 낮은 시황·공시 모음류 제목
# (제목에 해당 종목명이 없을 때만 버린다)
NOISE = re.compile(
    r"시황|마감|증시|코스피|코스닥|지수|\d{3,}\s*선|서킷\s*브레이커|사이드카|공시\s*\[|주요\s*공시|기업\s*공시|"
    r"특징주\s*모음|오늘의\s*(증시|주요)|헤드라인|브리핑|외국인.{0,6}(순매|팔자|사자|매물)|투자자별|\[속보\]|추천주|\d+\s*피\b|검은\s*.요일"
)


def parse_dt(s: str) -> datetime:
    return datetime.strptime(s[:12], "%Y%m%d%H%M").replace(tzinfo=KST)


def weekday_shift(d: datetime, step: int) -> datetime:
    d = d + timedelta(days=step)
    while d.weekday() >= 5:
        d = d + timedelta(days=step)
    return d


def window(date: str) -> tuple[datetime, datetime]:
    """직전 평일 15:30 ~ 다음 평일 08:30 (공휴일은 무시)."""
    d = datetime.strptime(date, "%Y%m%d").replace(tzinfo=KST)
    start = weekday_shift(d, -1).replace(hour=15, minute=30)
    end = weekday_shift(d, 1).replace(hour=8, minute=30)
    return start, end


def norm_title(t: str) -> str:
    return re.sub(r"[\W_]+", "", t)[:24]


def stock_news(code: str, start: datetime) -> list[dict]:
    items = []
    for page in range(1, MAX_PAGES + 1):
        groups = nget(f"/news/stock/{code}", {"pageSize": PAGE_SIZE, "page": page})
        batch = [it for g in groups or [] for it in g.get("items", [])]
        items += batch
        if len(batch) < PAGE_SIZE or not batch or parse_dt(batch[-1]["datetime"]) < start:
            break
        time.sleep(0.1)
    return items


def pick_codes(stocks: list[dict]) -> list[dict]:
    commons = [s for s in stocks if s["code"].endswith("0")] or stocks  # 우선주 제외(기사 중복)
    by_cap = sorted(commons, key=lambda s: -s["cap"])[:TOP_BY_CAP]
    by_move = sorted((s for s in commons if s["chg"] is not None and s not in by_cap),
                     key=lambda s: -abs(s["chg"]))[:TOP_BY_MOVE]
    return by_cap + by_move


def sector_articles(picks: list[dict], fetch, start: datetime, end: datetime) -> list[dict]:
    found: dict[str, dict] = {}
    seen_titles: dict[str, str] = {}
    for s in picks:
        for it in fetch(s["code"]):
            dt = parse_dt(it["datetime"])
            if not (start <= dt <= end):
                continue
            title = html.unescape(it.get("titleFull") or it.get("title") or "").strip()
            body = html.unescape(it.get("body") or "")
            if not title:
                continue
            in_title = s["name"] in title
            noisy = bool(NOISE.search(title))
            if noisy and not in_title:
                continue
            key = seen_titles.get(norm_title(title), it["id"])
            seen_titles.setdefault(norm_title(title), key)
            # 제목에 종목명 3점, 요약에만 1점, 둘 다 없으면 0점(맨 뒤로)
            score = (3 if in_title else 1 if s["name"] in body else 0) - (2 if noisy else 0)
            a = found.get(key)
            if a is None:
                found[key] = a = {
                    "title": title,
                    "office": it.get("officeName"),
                    "dt": dt.strftime("%Y%m%d%H%M"),
                    "url": it.get("mobileNewsUrl")
                    or f"https://n.news.naver.com/mnews/article/{it['officeId']}/{it['articleId']}",
                    "stocks": [],
                    "_score": 0,
                }
            if s["name"] not in a["stocks"]:
                a["stocks"].append(s["name"])
                a["_score"] += score + (1 if len(a["stocks"]) > 1 else 0)  # 여러 종목에 걸친 기사 가산
    ranked = sorted(found.values(), key=lambda a: (-a["_score"], -int(a["dt"])))
    out = ranked[:PER_SECTOR]
    for a in out:
        a.pop("_score")
    return out


def collect_news(mkt: str, date: str | None = None, force: bool = False) -> bool:
    d = DATA / mkt
    src = d / (f"{date}.json" if date else "latest.json")
    if not src.exists():
        log(f"  [{mkt}] 시세 파일 없음: {src.relative_to(ROOT)}")
        return False
    data = json.loads(src.read_text(encoding="utf-8"))
    date = data["date"]
    path = d / "news" / f"{date}.json"
    if path.exists() and not force:
        log(f"  {path.relative_to(ROOT)} 이미 있음 → 덮어쓰지 않음 (--force 로 강제)")
        return True

    start, end = window(date)
    sectors: dict[str, list[dict]] = {}
    for s in data["stocks"]:
        sectors.setdefault(s["sector"], []).append(s)

    cache: dict[str, list[dict]] = {}
    errors = 0

    def fetch(code):
        nonlocal errors
        if code not in cache:
            try:
                cache[code] = stock_news(code, start)
            except SourceError as e:
                errors += 1
                log(f"    뉴스 실패 {code}: {e}")
                cache[code] = []
            time.sleep(0.1)
        return cache[code]

    t0 = time.time()
    out = {name: sector_articles(pick_codes(lst), fetch, start, end) for name, lst in sectors.items()}
    if cache and errors == len(cache):
        log("  모든 뉴스 요청이 실패해서 저장하지 않습니다.")
        return False
    write_json(path, {
        "market": MARKETS[mkt]["code"],
        "date": date,
        "generated_at": now_kst().isoformat(timespec="seconds"),
        "source": "naver-stock-news",
        "window": [start.strftime("%Y%m%d%H%M"), end.strftime("%Y%m%d%H%M")],
        "sectors": out,
    })
    n = sum(len(v) for v in out.values())
    empty = sum(1 for v in out.values() if not v)
    log(f"  저장 {path.relative_to(ROOT)}: {len(out)}개 섹터, 기사 {n}건 (기사 없는 섹터 {empty}), "
        f"종목 {len(cache)}개 조회, {time.time() - t0:.0f}초")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["all", "kospi", "kosdaq"], default="all")
    ap.add_argument("--date", help="YYYYMMDD (기본: 각 시장 latest.json 의 날짜)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    mkts = ["kospi", "kosdaq"] if a.market == "all" else [a.market]
    ok = True
    for m in mkts:
        log(f"[{MARKETS[m]['code']}] 섹터 뉴스")
        ok = collect_news(m, a.date, a.force) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
