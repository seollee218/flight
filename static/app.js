// ─────────────────────────────────────────
// 상태
// ─────────────────────────────────────────
let currentMonitorId = null;
let monitorInterval = null;
let monitorCycle = 0;
let totalFound = 0;
let lastSearchParams = null;

// ─────────────────────────────────────────
// 초기화
// ─────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    // 날짜 기본값: 내일
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 14);
    document.getElementById("date").value = tomorrow.toISOString().split("T")[0];

    // 칩 토글
    document.querySelectorAll(".chip").forEach(chip => {
        chip.addEventListener("click", () => {
            const cb = chip.querySelector("input");
            cb.checked = !cb.checked;
            chip.classList.toggle("selected", cb.checked);
        });
    });
});

// ─────────────────────────────────────────
// 유틸
// ─────────────────────────────────────────
function swapAirports() {
    const dep = document.getElementById("departure");
    const arr = document.getElementById("arrival");
    const tmp = dep.value;
    dep.value = arr.value;
    arr.value = tmp;
}

function toggleReturnDate() {
    const trip = document.getElementById("trip").value;
    document.getElementById("returnDateGroup").style.display = trip === "RT" ? "block" : "none";
}

function getSelectedSeatClasses() {
    const selected = [];
    document.querySelectorAll(".chip input:checked").forEach(cb => {
        selected.push(cb.value);
    });
    return selected.length > 0 ? selected : ["Y"];
}

function formatPrice(price) {
    if (!price || price === 0) return "가격미정";
    return price.toLocaleString() + "원";
}

function getSearchParams() {
    const dep = document.getElementById("departure").value.toUpperCase().trim();
    const arr = document.getElementById("arrival").value.toUpperCase().trim();
    const date = document.getElementById("date").value;
    const trip = document.getElementById("trip").value;
    const returnDate = document.getElementById("returnDate").value;
    const seatClasses = getSelectedSeatClasses();
    const timeStart = document.getElementById("timeStart").value;
    const timeEnd = document.getElementById("timeEnd").value;
    let timeRange = "";
    if (timeStart && timeEnd) {
        timeRange = `${timeStart}~${timeEnd}`;
    }

    // 검증
    if (!/^[A-Z]{3}$/.test(dep)) { alert("출발지: 영문 3자리 IATA 코드를 입력하세요."); return null; }
    if (!/^[A-Z]{3}$/.test(arr)) { alert("도착지: 영문 3자리 IATA 코드를 입력하세요."); return null; }
    if (!date) { alert("출발 날짜를 선택하세요."); return null; }
    if (trip === "RT" && !returnDate) { alert("왕복인 경우 귀국 날짜를 선택하세요."); return null; }

    return { departure: dep, arrival: arr, date, trip, return_date: returnDate, seat_classes: seatClasses, time_range: timeRange };
}

// ─────────────────────────────────────────
// 검색
// ─────────────────────────────────────────
async function searchFlights() {
    const params = getSearchParams();
    if (!params) return;

    lastSearchParams = params;

    // UI 토글
    document.getElementById("loading").style.display = "block";
    document.getElementById("results").style.display = "none";
    document.getElementById("monitorPanel").style.display = "none";

    try {
        const resp = await fetch("/api/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(params),
        });
        const data = await resp.json();

        document.getElementById("loading").style.display = "none";
        document.getElementById("results").style.display = "block";

        // 제목
        document.getElementById("resultsTitle").textContent =
            `${params.departure} → ${params.arrival}  ${params.date}`;
        document.getElementById("resultsCount").textContent = `${data.count}건`;

        // AI 요약
        if (data.summary) {
            document.getElementById("summaryBox").style.display = "block";
            document.getElementById("summaryText").textContent = data.summary;
        } else {
            document.getElementById("summaryBox").style.display = "none";
        }

        // 테이블
        const tbody = document.getElementById("flightsBody");
        tbody.innerHTML = "";
        if (data.flights.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:24px;color:#999;">조건에 맞는 항공편이 없습니다.</td></tr>`;
        } else {
            data.flights.forEach((f, i) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${i + 1}</td>
                    <td>${f.airline_name}</td>
                    <td>${f.flight_no}</td>
                    <td>${f.dep_time}</td>
                    <td>${f.arr_time}</td>
                    <td>${f.fare_class}</td>
                    <td>${f.seats}</td>
                    <td>${formatPrice(f.price)}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        // 모니터링 영역
        document.getElementById("monitorActions").style.display = "block";

    } catch (err) {
        document.getElementById("loading").style.display = "none";
        alert("조회 중 오류가 발생했습니다: " + err.message);
    }
}

// ─────────────────────────────────────────
// 모니터링
// ─────────────────────────────────────────
async function startMonitoring() {
    if (!lastSearchParams) return;

    // 기존 모니터 중지
    if (monitorInterval) {
        clearInterval(monitorInterval);
    }

    try {
        const resp = await fetch("/api/monitor/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(lastSearchParams),
        });
        const data = await resp.json();
        currentMonitorId = data.monitor_id;
        monitorCycle = 0;
        totalFound = 0;

        // UI
        document.getElementById("monitorActions").style.display = "none";
        document.getElementById("monitorPanel").style.display = "block";
        document.getElementById("monitorLogBody").innerHTML = "";
        document.getElementById("monitorEmpty").style.display = "block";
        document.getElementById("monitorStatus").textContent = "● 활성";
        document.getElementById("monitorStatus").className = "status-badge active";

        // 주기적 체크 (10초 간격 - 데모용)
        monitorInterval = setInterval(checkMonitor, 10000);

    } catch (err) {
        alert("모니터링 시작 실패: " + err.message);
    }
}

async function checkMonitor() {
    if (!currentMonitorId) return;

    monitorCycle++;
    document.getElementById("monitorCycle").textContent = `조회 #${monitorCycle}`;

    try {
        const resp = await fetch(`/api/monitor/${currentMonitorId}/check`);
        const data = await resp.json();

        if (!data.active) {
            stopMonitoring();
            return;
        }

        if (data.new_flights && data.new_flights.length > 0) {
            totalFound += data.new_flights.length;
            document.getElementById("monitorFound").textContent = `발견: ${totalFound}건`;
            document.getElementById("monitorEmpty").style.display = "none";

            const tbody = document.getElementById("monitorLogBody");
            data.new_flights.forEach(f => {
                const tr = document.createElement("tr");
                tr.className = "new-row";
                const now = new Date().toLocaleTimeString("ko-KR");
                tr.innerHTML = `
                    <td>${now}</td>
                    <td>${f.airline_name}</td>
                    <td>${f.flight_no}</td>
                    <td>${f.dep_time}</td>
                    <td>${f.arr_time}</td>
                    <td>${f.fare_class}</td>
                    <td>${f.seats}</td>
                    <td>${formatPrice(f.price)}</td>
                `;
                tbody.insertBefore(tr, tbody.firstChild);
            });

            // 브라우저 알림
            if (Notification.permission === "granted") {
                new Notification(`✈ 빈자리 ${data.new_flights.length}건 발견!`, {
                    body: data.new_flights.map(f => `${f.airline_name} ${f.dep_time} ${formatPrice(f.price)}`).join("\n"),
                });
            }
        }

    } catch (err) {
        console.error("모니터링 체크 실패:", err);
    }
}

function stopMonitoring() {
    if (monitorInterval) {
        clearInterval(monitorInterval);
        monitorInterval = null;
    }

    if (currentMonitorId) {
        fetch(`/api/monitor/${currentMonitorId}/stop`, { method: "POST" });
    }

    document.getElementById("monitorStatus").textContent = "■ 중지됨";
    document.getElementById("monitorStatus").className = "status-badge stopped";

    // 다시 검색할 수 있도록
    document.getElementById("monitorActions").style.display = "block";
}

// 브라우저 알림 권한 요청
if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
}