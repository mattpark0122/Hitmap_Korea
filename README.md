# 한국 증시 히트맵 (코스피 · 코스닥)

## 1. 데이터 수집

```bash
pip install requests pykrx
python scripts/collect.py                  # 두 시장, 직전(또는 오늘 마감) 거래일
python scripts/collect.py --market kosdaq  # 한 시장만
python scripts/collect.py --force          # 같은 날짜 파일 다시 받기
python scripts/collect.py --date 20261105  # 과거 날짜 (pykrx + KRX 로그인 필요)
python scripts/collect.py --no-news        # 섹터 기사 수집 생략
python scripts/news.py --force             # 섹터 기사만 다시 수집
```

결과
- `data/{kospi|kosdaq}/YYYYMMDD.json` — 날짜별, 이미 있으면 덮어쓰지 않음
- `data/{kospi|kosdaq}/latest.json` — 가장 최근 날짜 파일의 사본
- `data/{kospi|kosdaq}/dates.json` — 보유 날짜 목록 (페이지의 날짜 선택용)
- `data/{kospi|kosdaq}/news/YYYYMMDD.json` — 섹터별 관련 기사 (아래 참고)

각 파일: 지수(`index`: 종가·대비·등락률), 요약(`summary`), 종목 배열(`stocks`: `code`, `name`, `close`(원), `chg`(%), `cap`(억원), `sector`).

휴장일(주말·공휴일)이나 장 마감 전에 실행하면 직전 거래일로 처리하고, 그 파일이 이미 있으면 건드리지 않습니다.

### 데이터 소스

| 순위 | 소스 | 조건 |
|---|---|---|
| 1 | **pykrx** (KRX 정보데이터시스템) | KRX가 로그인을 요구하도록 바뀌어서 `KRX_ID`, `KRX_PW`가 필요합니다. 환경변수나 프로젝트 루트 `.env`에 넣으세요. 과거 날짜 수집은 이 경로만 됩니다. |
| 2 | **네이버 금융** 모바일 API | 로그인 불필요. 최신 거래일만 제공하고, 장 마감(15:30) 뒤에 실행해야 합니다. |

KRX 계정은 **KRX Data Marketplace**(data.krx.co.kr) 로그인 화면의 '회원가입'에서 만듭니다. 네이버·카카오로 가입했다면 '마이페이지 > 정보수정'에서 아이디/비밀번호를 따로 설정해야 pykrx가 로그인할 수 있습니다. 로그인 세션은 1시간마다 자동 갱신됩니다.

`.env` 예시 (git에는 올라가지 않음):
```
KRX_ID=아이디
KRX_PW=비밀번호
```

### 업종(섹터) 우선순위
KRX 업종분류(pykrx) → 네이버 업종(79개, 코스닥 포함 전 종목) → 우선주는 보통주의 업종 → `reference/kospi-heatmap.html`의 매핑 → `기타`.
파일의 `source.sectors`에 출처별 종목 수가 남습니다.

### 섹터 기사
섹터마다 시총 상위 3종목과 등락률 절댓값 상위 2종목(우선주 제외)의 네이버 종목 뉴스를 모읍니다. 그중 거래일 전후(직전 평일 15:30 ~ 다음 평일 08:30) 기사만 남기고, 중복 기사와 종목명 없는 시황·공시 모음 기사를 걸러 섹터당 최대 5건을 저장합니다.
- 저작권 때문에 본문은 저장하지 않고 제목·언론사·시각·원문 링크만 저장합니다.
- 네이버 비공식 API라서 구조가 바뀌면 고쳐야 합니다. 기사 수집이 실패해도 시세 수집은 실패로 처리하지 않습니다.
- 장 마감 직후에 수집하면 그날 저녁부터 다음 날 아침까지의 기사는 빠집니다. 필요하면 다음 날 아침에 `python scripts/news.py --force`로 다시 받으세요.

## 2. 히트맵 페이지

`fetch`로 JSON을 읽기 때문에 로컬 서버로 열어야 합니다.

```bash
python -m http.server 8000
```
→ http://localhost:8000/#kospi 또는 http://localhost:8000/#kosdaq (특정 날짜: `#kosdaq/20261105`)

- 섹터 순위: '섹터 정렬'에서 고릅니다. 기준은 섹터 전체 종목의 시총가중 평균 등락률이고, 종목 수(상위 N)로 자르기 전에 계산합니다.
  - 변동폭 순(기본): 등락률 절댓값이 큰 순
  - 상승률 순: 많이 오른 순. 오른 섹터가 없으면 덜 떨어진 섹터부터 나오고 안내 문구가 붙습니다.
  - 하락률 순: 많이 내린 순. 내린 섹터가 없으면 덜 오른 섹터부터 나옵니다.
- 표시 섹터 수(5/10/15/20/전체)로 고른 섹터만 그리고, 트리맵 안 배치는 시총 크기 기준입니다.
- 섹터 제목(트리맵)이나 아래 섹터 목록을 누르면 관련 기사 패널이 열립니다. 데스크톱은 오른쪽 패널, 모바일은 하단 시트로 열리고 Esc나 ✕로 닫습니다.
- 매일 자동 수집은 아래 GitHub Actions가 합니다. 로컬에서만 쓸 때는 Windows 작업 스케줄러 등으로 평일 16:00에 `python scripts/collect.py`를 돌리면 됩니다.

## 3. 웹 배포 (GitHub Pages + Actions)

`.github/workflows/pages.yml` 하나로 수집과 배포를 함께 합니다.

| 언제 | 하는 일 |
|---|---|
| `main`에 push | 수집 없이 바로 배포 |
| 평일 16:05 KST | `collect.py`(시세 + 섹터 기사) → `data/` 커밋 → 배포 |
| 평일 08:40 KST | `news.py --force`(전날 저녁~아침 기사 보강) → 커밋 → 배포 |
| Actions 탭에서 Run workflow | `collect.py` → 커밋 → 배포 |

처음 한 번 설정
1. 저장소 **Settings → General → Danger Zone → Change visibility**에서 Public으로 전환 (무료 요금제의 Pages 조건)
2. **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 선택
3. (선택) **Settings → Secrets and variables → Actions**에 `KRX_ID`, `KRX_PW` 추가. 없으면 네이버 금융으로 수집합니다.
4. **Actions → Collect & deploy → Run workflow**로 한 번 실행해 수집·배포가 되는지 확인

주소: `https://<아이디>.github.io/Hitmap_Korea/#kospi`

- 사이트에는 `index.html`, `assets/`, `data/`만 올라갑니다.
- 수집이 실패하면 그 회차는 배포하지 않고, 이전 데이터가 그대로 보입니다. Actions 탭에서 로그를 확인하세요.
- GitHub cron은 UTC 기준이며 몇 분~수십 분 늦게 시작될 수 있습니다. 공휴일에는 `collect.py`가 직전 거래일을 처리하고 이미 있는 파일은 건드리지 않습니다.
- GitHub 서버는 해외에 있어서 네이버 API가 응답하지 않으면 수집이 실패할 수 있습니다. 그때는 `KRX_ID`/`KRX_PW`를 넣거나 로컬에서 수집 후 push하세요.
