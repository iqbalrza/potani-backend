# Potani Backend + Agent

Implementasi [PRD_Potani_Backend_Agent.md](PRD_Potani_Backend_Agent.md). Satu codebase, tiga proses:

| Proses | Perintah |
|---|---|
| P1 API | `uvicorn app.main:app` |
| P2 MQTT worker | `python -m app.workers.mqtt_worker` |
| P3 Scheduler | `python -m app.workers.scheduler_worker` |

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # Windows; Linux/macOS: .venv/bin/pip
cp .env.example .env                                  # lalu isi
alembic upgrade head
```

## Menjalankan test

```bash
.venv/Scripts/python -m pytest
```

Test memakai SQLite in-memory, `AUTH_MODE=dev`, LLM palsu, dan data uji `tests/fixtures/*.test.json`
(tidak butuh PostgreSQL, Groq, Firebase, atau broker MQTT). Cakupan sesuai PRD bagian 11:
s(x) di 5+ titik, parameter tanpa sumber, filter cahaya, verdict evaluator, validator rencana,
payload MQTT valid/invalid/duplikat/dengan-tanpa sesi, semua transisi F6 termasuk `ESCALATED`
pada revisi ketiga, dan semua endpoint inti tetap 200 dengan `narrative: null` saat LLM gagal.

## Konsol test web

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_console.ps1   # tambah -Reset untuk database baru
```

Buka **http://localhost:8000/console** (pengguna `demo`). Konsol memanggil API yang sama dengan aplikasi
mobile dan mencakup: pairing & pot, simulasi probe lewat sesi (logika ingest P2 yang sama), rekomendasi F2
(termasuk pertanyaan `ask_user`), evaluasi F3, rencana & checkpoint F6, TaniBot F7, log `agent_logs`, dan
log setiap request. Tombol "dev" menjalankan job scheduler dan memajukan waktu checkpoint sehingga satu
putaran closed-loop bisa dites dalam hitungan detik.

Konsol dan endpoint `/dev/*` hanya terpasang bila `AUTH_MODE=dev`; di mode `firebase` keduanya tidak ada.

## Coba lokal tanpa Docker (SQLite + mode dev)

```powershell
$env:DATABASE_URL="sqlite:///./potani-dev.db"; $env:AUTH_MODE="dev"
$env:CROPS_FILE="crops.dev.json"; $env:REMEDIATION_FILE="remediation_rules.dev.json"
$env:TIME_ACCELERATION="1440"        # 1 hari = 1 menit (mode demo)
alembic upgrade head
python -m app.seed_demo --uid demo   # user/perangkat/pot demo (is_demo)
uvicorn app.main:app --reload        # dokumentasi interaktif: http://localhost:8000/docs
python -m app.workers.scheduler_worker
```

Token mode dev: `Authorization: Bearer dev:demo`. Kirim bacaan probe tanpa perangkat:

```bash
# buka sesi dulu: POST /sessions {"device_id": "potani-demo-01", "pot_id": "...", "stage": "pre_plant"}
python scripts/simulate_probe.py --device potani-demo-01 --ph 6.2 --moisture 40 --direct
# atau lewat broker: --host localhost --port 1883 --no-tls
```

Stack lengkap dengan PostgreSQL + Mosquitto: `docker compose up --build`.

## Data tanaman dan dosis

- `app/domain/crops/crops.json` dan `remediation_rules.json` sengaja berisi `null`. **Tim yang mengisi
  angka dari FAO ECOCROP dan Djaenudin dkk.** Parameter/opsi tanpa `source` otomatis dilewati
  dan dilaporkan sebagai "belum ada acuan".
- `*.dev.json` berisi angka contoh berflag `is_demo` untuk pengembangan; jangan dipakai untuk produksi
  atau dilaporkan sebagai hasil. Respons API membawa `is_demo: true` bila data demo aktif.

## LLM (Groq)

Isi `GROQ_API_KEY` di `.env`. Default `LLM_MODEL=openai/gpt-oss-20b`; model default di PRD
(`llama-3.1-8b-instant`) sudah tidak tersedia di Groq. Model chat yang tersedia per Okt 2026:
`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` (ketiganya mendukung tool calling dan JSON mode).

**Batas akun gratis: 8.000 token/menit per model.** Satu panggilan TaniBot ±2.000–3.000 token, dan satu pesan bisa
butuh 2–3 panggilan, jadi akun gratis hanya kuat sekitar 1–2 pesan TaniBot per menit. Saat 429, klien menunggu
sesuai `retry-after` (maks `LLM_RATE_LIMIT_WAIT_S`, default 10 detik) lalu jatuh ke hasil deterministik
(`narrative: null`). Untuk demo final dengan beberapa penguji, pakai tier berbayar Groq.

## Skrip riset

| RQ | Perintah |
|---|---|
| RQ1 | `python -m app.ml.noise_stability [--csv data.csv]` |
| RQ3 | `python tests/agent_eval/run_eval.py --models <model...> --delay 18` (butuh `GROQ_API_KEY`; 50 skenario di `scenarios.jsonl`; `--delay` agar tidak terkena batas token/menit akun gratis). Catatan: skrip hanya menilai tool *pertama*, sehingga rantai wajar seperti `get_latest_reading` → `run_evaluator` dihitung salah |
| RQ5 | `python scripts/rq5_report.py [--include-demo]` |

## Keputusan implementasi di luar teks PRD

- Tabel tambahan `agent_questions` untuk `ask_user` asinkron; jawaban lewat `POST /agent/answers`
  melanjutkan `run_id` yang sama.
- State rencana tambahan: `WAITING_USER` (menunggu jawaban `ask_user`) dan `CLOSED` (agent memanggil
  `log_outcome`). Transisi `VERIFYING → AWAITING_CHECKPOINT` dipakai bila rencana punya beberapa
  checkpoint dan sebagian belum diukur.
- Status checkpoint tambahan `cancelled` untuk checkpoint versi lama saat rencana direvisi.
- Nilai yang berjarak ≤ MAE dari batas optimal diberi status `near_boundary` dan tidak dianggap
  `low`/`high` (ketidakpastian sensor); pengguna diminta ukur ulang.
- Aturan 2 ditegakkan secara deterministik: angka di teks LLM yang tidak ada di output tool
  menyebabkan narasi dibuang (`narrative: null`) atau TaniBot diminta menulis ulang sekali.
- Tool write TaniBot (`send_reminder`, `schedule_checkpoint`) hanya diaktifkan bila pesan memuat
  kata seperti "ingatkan", "pengingat", atau "jadwalkan".
- Endpoint tambahan: `GET/PATCH /me`, `GET /devices`, `GET /pots/{id}`, `GET /plans`.
- Skema tool: field opsional menerima `null` (Groq menolak tool call dengan 400 `tool_use_failed` bila model
  mengisi null pada field bertipe ketat); `tool_use_failed` juga dicoba ulang.
- Validator: setiap target wajib punya checkpoint (bila daftar checkpoint diberikan); rencana LLM F2 hanya boleh
  menargetkan parameter yang bermasalah; revisi F6 dari LLM ditolak bila sama persis dengan versi sebelumnya.
- Evaluator menyebut sisi batas (`boundary_side`, `message`) secara deterministik agar narasi LLM tidak salah arah.
