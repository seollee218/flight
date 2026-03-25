import os
os.environ["no_proxy"] = ""
os.environ["NO_PROXY"] = ""

import json
import random
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import yaml
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ─────────────────────────────────────────────
# 로깅
# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flight-alert")

load_dotenv()
CONFIG: dict = {}


def load_config():
    global CONFIG
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            CONFIG = yaml.safe_load(f) or {}
    else:
        CONFIG = {}
    CONFIG.setdefault("llm", {})
    CONFIG["llm"]["endpoint"] = os.getenv("LLM_ENDPOINT", CONFIG.get("llm", {}).get("endpoint", ""))
    CONFIG["llm"]["api_key"] = os.getenv("LLM_API_KEY", CONFIG.get("llm", {}).get("api_key", ""))
    CONFIG["llm"]["model"] = os.getenv("LLM_MODEL", CONFIG.get("llm", {}).get("model", ""))
    CONFIG.setdefault("monitor", {})
    CONFIG["monitor"]["interval_seconds"] = int(os.getenv("MONITOR_INTERVAL", CONFIG.get("monitor", {}).get("interval_seconds", 60)))
    CONFIG["monitor"]["max_hours"] = int(os.getenv("MONITOR_MAX_HOURS", CONFIG.get("monitor", {}).get("max_hours", 24)))


load_config()

# ─────────────────────────────────────────────
# FastAPI 앱
# ─────────────────────────────────────────────
app = FastAPI(title="항공권 빈자리 알림", version="1.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ─────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────
FARE_TYPE_DISPLAY = {"Y": "이코노미", "PE": "프리미엄이코노미", "C": "비즈니스", "F": "퍼스트"}

AIRLINES = [
    "대한항공", "아시아나항공", "제주항공", "진에어", "티웨이항공",
    "에어서울", "에어부산", "이스타항공", "일본항공(JAL)", "전일본공수(ANA)",
    "피치항공", "중국동방항공", "중국남방항공", "캐세이퍼시픽",
    "싱가포르항공", "타이항공", "베트남항공", "에미레이트항공",
    "루프트한자", "에어프랑스", "델타항공", "유나이티드항공",
]

AIRLINE_CODES = [
    "KE", "OZ", "7C", "LJ", "TW", "RS", "BX", "ZE",
    "JL", "NH", "MM", "MU", "CZ", "CX",
    "SQ", "TG", "VN", "EK", "LH", "AF", "DL", "UA",
]

AIRPORT_PAIRS = {
    "ICN-NRT": ("인천", "나리타"),
    "ICN-KIX": ("인천", "간사이"),
    "ICN-FUK": ("인천", "후쿠오카"),
    "ICN-BKK": ("인천", "방콕"),
    "ICN-SIN": ("인천", "싱가포르"),
    "ICN-DAD": ("인천", "다낭"),
    "ICN-CDG": ("인천", "파리"),
    "ICN-JFK": ("인천", "뉴욕"),
    "ICN-LAX": ("인천", "로스앤젤레스"),
    "GMP-HND": ("김포", "하네다"),
}

# ─────────────────────────────────────────────
# 모니터링 상태 저장 (인메모리)
# ─────────────────────────────────────────────
monitors: dict = {}  # monitor_id -> { params, notified, active, results_log }
monitor_counter = 0


# ─────────────────────────────────────────────
# 목업 데이터 생성
# ─────────────────────────────────────────────
def generate_mock_flights(departure, arrival, date, seat_classes, time_range=None):
    """실제 API 대신 리얼한 목업 데이터 생성"""
    flights = []
    base_prices = {"Y": 180000, "PE": 450000, "C": 1200000, "F": 3500000}

    pair_key = f"{departure}-{arrival}"
    distance_factor = {
        "NRT": 1.0, "KIX": 1.0, "FUK": 0.85, "HND": 0.95,
        "BKK": 1.4, "SIN": 1.5, "DAD": 1.1,
        "CDG": 3.2, "JFK": 3.5, "LAX": 3.3,
    }.get(arrival, 1.5)

    hours = [
        ("06:30", "09:00"), ("07:10", "09:45"), ("07:55", "10:35"),
        ("08:10", "10:40"), ("08:50", "11:20"), ("09:20", "11:50"),
        ("09:50", "12:00"), ("10:20", "12:55"), ("10:25", "12:55"),
        ("11:20", "13:50"), ("12:55", "15:30"), ("13:20", "15:50"),
        ("13:45", "16:15"), ("14:45", "17:15"), ("15:00", "17:30"),
        ("15:00", "17:40"), ("15:35", "18:05"), ("16:05", "18:30"),
        ("16:15", "18:55"), ("16:45", "19:10"), ("17:15", "19:45"),
        ("18:15", "20:45"), ("18:35", "21:05"), ("19:00", "21:30"),
    ]

    for sc in seat_classes:
        base = base_prices.get(sc, 200000)
        num_flights = random.randint(8, 16)
        chosen_hours = random.sample(hours, min(num_flights, len(hours)))

        for dep_t, arr_t in chosen_hours:
            airline_idx = random.randint(0, len(AIRLINES) - 1)
            price_var = random.uniform(0.7, 2.2)
            price = int(base * distance_factor * price_var)
            price = (price // 100) * 100  # 100원 단위

            avail = random.choices(
                ["정보없음", "1석", "2석", "3석", "4석", "5석", "9석"],
                weights=[30, 10, 10, 10, 10, 15, 15],
            )[0]

            fno = f"{AIRLINE_CODES[airline_idx]}{random.randint(100,9999)}"

            flights.append({
                "id": f"{fno}_{dep_t}_{sc}_{random.randint(1000,9999)}",
                "airline": AIRLINE_CODES[airline_idx],
                "airline_name": AIRLINES[airline_idx],
                "flight_no": fno,
                "dep_time": dep_t,
                "arr_time": arr_t,
                "dep_airport": departure,
                "arr_airport": arrival,
                "price": price,
                "seats": avail,
                "fare_class": FARE_TYPE_DISPLAY.get(sc, sc),
            })

    # 시간 필터
    if time_range:
        start_str, end_str = time_range.split("~")
        sh, sm = map(int, start_str.strip().split(":"))
        eh, em = map(int, end_str.strip().split(":"))
        s_min, e_min = sh * 60 + sm, eh * 60 + em
        flights = [f for f in flights if s_min <= int(f["dep_time"][:2]) * 60 + int(f["dep_time"][3:5]) <= e_min]

    # 중복 제거
    seen, unique = set(), []
    for f in flights:
        k = f"{f['flight_no']}_{f['dep_time']}"
        if k not in seen:
            seen.add(k)
            unique.append(f)

    unique.sort(key=lambda x: x["price"])
    return unique


def generate_new_mock_flights(departure, arrival, date, seat_classes, existing_ids, time_range=None):
    """모니터링용: 기존에 없던 새 항공편을 확률적으로 생성"""
    if random.random() > 0.35:  # 35% 확률로 새 빈자리 발견
        return []

    new_count = random.randint(1, 3)
    base_prices = {"Y": 180000, "PE": 450000, "C": 1200000, "F": 3500000}
    distance_factor = {
        "NRT": 1.0, "KIX": 1.0, "FUK": 0.85, "BKK": 1.4,
        "SIN": 1.5, "DAD": 1.1, "CDG": 3.2, "JFK": 3.5, "LAX": 3.3,
    }.get(arrival, 1.5)

    new_flights = []
    for _ in range(new_count):
        sc = random.choice(seat_classes)
        base = base_prices.get(sc, 200000)
        price = int(base * distance_factor * random.uniform(0.5, 1.5))
        price = (price // 100) * 100

        h = random.randint(6, 21)
        m = random.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55])
        dep_t = f"{h:02d}:{m:02d}"
        arr_h = h + random.randint(2, 4)
        arr_m = random.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55])
        arr_t = f"{min(arr_h, 23):02d}:{arr_m:02d}"

        airline_idx = random.randint(0, len(AIRLINES) - 1)
        fno = f"{AIRLINE_CODES[airline_idx]}{random.randint(100,9999)}"
        fid = f"{fno}_{dep_t}_{sc}_{random.randint(10000,99999)}"

        if fid not in existing_ids:
            new_flights.append({
                "id": fid,
                "airline": AIRLINE_CODES[airline_idx],
                "airline_name": AIRLINES[airline_idx],
                "flight_no": fno,
                "dep_time": dep_t,
                "arr_time": arr_t,
                "dep_airport": departure,
                "arr_airport": arrival,
                "price": price,
                "seats": random.choice(["1석", "2석", "3석"]),
                "fare_class": FARE_TYPE_DISPLAY.get(sc, sc),
            })
    return new_flights


# ─────────────────────────────────────────────
# LLM 요약
# ─────────────────────────────────────────────
async def summarize_with_llm(flights, search_info):
    llm = CONFIG.get("llm", {})
    if not llm.get("endpoint") or not llm.get("api_key"):
        return _fallback_summary(flights, search_info)
    ft = ""
    for i, f in enumerate(flights[:10], 1):
        p = f"{f['price']:,}원" if f["price"] > 0 else "가격미정"
        ft += f"{i}. {f['airline_name']}({f['flight_no']}) 출발 {f['dep_time']} 도착 {f['arr_time']} 잔여석 {f['seats']} 가격 {p}\n"
    prompt = f"{search_info['departure']}→{search_info['arrival']} {search_info['date']} 항공권 조회 결과:\n\n{ft}\n위 결과를 한국어로 친절하게 요약해주세요."
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(llm["endpoint"],
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {llm['api_key']}"},
                json={"model": llm["model"], "messages": [
                    {"role": "system", "content": "항공권 추천 어시스턴트입니다."},
                    {"role": "user", "content": prompt}
                ], "max_tokens": 1024, "temperature": 0.7})
            r.raise_for_status()
            return r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.warning(f"LLM 요약 실패: {e}")
        return _fallback_summary(flights, search_info)


def _fallback_summary(flights, search_info):
    if not flights:
        return "조건에 맞는 항공편이 없습니다."
    cheapest = flights[0]
    most_expensive = flights[-1]
    p1 = f"{cheapest['price']:,}원"
    p2 = f"{most_expensive['price']:,}원"
    classes = list(set(f["fare_class"] for f in flights))
    return (
        f"{search_info['departure']}→{search_info['arrival']} {search_info['date']} "
        f"총 {len(flights)}개 항공편이 검색되었습니다. "
        f"최저가는 {cheapest['airline_name']} {cheapest['flight_no']}편 "
        f"{cheapest['dep_time']} 출발, {p1}이며, "
        f"최고가는 {p2}입니다. "
        f"좌석등급: {', '.join(classes)}."
    )


# ─────────────────────────────────────────────
# API 요청 모델
# ─────────────────────────────────────────────
class SearchRequest(BaseModel):
    departure: str
    arrival: str
    date: str
    trip: str = "OW"
    return_date: str = ""
    seat_classes: list = ["Y"]
    time_range: str = ""


class MonitorRequest(BaseModel):
    departure: str
    arrival: str
    date: str
    trip: str = "OW"
    return_date: str = ""
    seat_classes: list = ["Y"]
    time_range: str = ""


# ─────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/search")
async def search_flights(req: SearchRequest):
    flights = generate_mock_flights(
        req.departure, req.arrival, req.date,
        req.seat_classes, req.time_range or None,
    )
    search_info = {
        "departure": req.departure,
        "arrival": req.arrival,
        "date": req.date,
        "fare_class": ", ".join([FARE_TYPE_DISPLAY.get(s, s) for s in req.seat_classes]),
    }
    summary = await summarize_with_llm(flights, search_info)
    return JSONResponse({
        "flights": flights,
        "summary": summary,
        "count": len(flights),
    })


@app.post("/api/monitor/start")
async def start_monitor(req: MonitorRequest):
    global monitor_counter
    monitor_counter += 1
    mid = f"mon_{monitor_counter}"

    monitors[mid] = {
        "params": req.dict(),
        "notified": set(),
        "active": True,
        "results_log": [],
        "started_at": datetime.now().isoformat(),
    }

    # 초기 검색 결과의 ID를 notified에 추가 (중복 방지)
    initial = generate_mock_flights(
        req.departure, req.arrival, req.date,
        req.seat_classes, req.time_range or None,
    )
    for f in initial:
        monitors[mid]["notified"].add(f["id"])

    return JSONResponse({
        "monitor_id": mid,
        "message": f"모니터링 시작! (ID: {mid})",
        "initial_count": len(initial),
    })


@app.get("/api/monitor/{monitor_id}/check")
async def check_monitor(monitor_id: str):
    mon = monitors.get(monitor_id)
    if not mon:
        return JSONResponse({"error": "모니터링을 찾을 수 없습니다."}, status_code=404)
    if not mon["active"]:
        return JSONResponse({"new_flights": [], "message": "모니터링이 중지되었습니다.", "active": False})

    p = mon["params"]
    new_flights = generate_new_mock_flights(
        p["departure"], p["arrival"], p["date"],
        p["seat_classes"], mon["notified"],
        p.get("time_range") or None,
    )

    for f in new_flights:
        mon["notified"].add(f["id"])
        mon["results_log"].append({**f, "found_at": datetime.now().isoformat()})

    return JSONResponse({
        "new_flights": new_flights,
        "total_notified": len(mon["notified"]),
        "active": True,
    })


@app.post("/api/monitor/{monitor_id}/stop")
async def stop_monitor(monitor_id: str):
    mon = monitors.get(monitor_id)
    if not mon:
        return JSONResponse({"error": "모니터링을 찾을 수 없습니다."}, status_code=404)
    mon["active"] = False
    return JSONResponse({
        "message": "모니터링이 중지되었습니다.",
        "total_found": len(mon["results_log"]),
    })


@app.get("/api/monitor/{monitor_id}/log")
async def get_monitor_log(monitor_id: str):
    mon = monitors.get(monitor_id)
    if not mon:
        return JSONResponse({"error": "모니터링을 찾을 수 없습니다."}, status_code=404)
    return JSONResponse({
        "log": mon["results_log"],
        "active": mon["active"],
        "started_at": mon["started_at"],
    })


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)