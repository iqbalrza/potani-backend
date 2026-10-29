# PRD Potani: Backend + Agent

Versi 0.1 · 29 Oktober 2026 · Status: draft untuk implementasi
Pemilik: Tim SayangTanah (UNIKOM) · Repo acuan: https://github.com/iqbalrza/GEMASTIK-2026

> Dokumen ini ditulis untuk dipakai sebagai panduan vibe coding. Bagian 2 (Aturan wajib) harus dipatuhi oleh siapa pun atau AI apa pun yang menulis kode. Bila ada konflik antara PRD dan kode lama di repo, PRD yang berlaku.

---

## 1. Konteks dan tujuan

Potani adalah pendamping tanam cerdas untuk penanam pekarangan dan pot di kota. Alat genggam berbasis ESP32 mengukur pH, kelembapan tanah, dan suhu tanah; backend menilai kecocokan tanaman, menyusun rencana, dan memverifikasi hasil lewat pengukuran ulang.

Backend ini menggantikan prototipe di repo acuan (FastAPI dengan 3 mode, tanpa database, tanpa subscriber MQTT, TaniBot tanpa tool calling).

**Tujuan rilis ini (P0, untuk demo final 10 sampai 13 November 2026):**

1. Menerima data probe lewat MQTT dan menautkannya ke pot milik pengguna.
2. Soil-first: skor kesesuaian berbasis pengetahuan + rencana tanam (workflow).
3. Plant-first: evaluasi kecocokan tanaman pilihan + saran perbaikan bersumber.
4. Checkpoint dan closed-loop verification (agent) dengan riwayat per pot.
5. TaniBot dengan tool calling.

**Di luar lingkup rilis ini:** deteksi daun (F8), dashboard penyuluh, retrain model otomatis, pembayaran, multi-bahasa.

---

## 2. Aturan wajib (non-negotiable)

1. **Keputusan cocok atau tidak hanya dari kode deterministik** (skor kesesuaian, evaluator, rule). LLM tidak boleh menentukan kecocokan, rentang, angka, atau dosis.
2. **LLM hanya boleh memakai data dari hasil tool.** Semua angka di jawaban LLM harus berasal dari output tool pada percakapan yang sama.
3. **Setiap rentang dan dosis wajib punya field `source`.** Data tanpa sumber disimpan dengan `source: null` dan ditandai `unsourced: true`; parameter itu dilewati dalam penilaian dan dilaporkan sebagai "belum ada acuan".
4. **Sistem tetap berfungsi tanpa LLM.** Bila Groq gagal, endpoint mengembalikan hasil deterministik dengan `narrative: null`.
5. **Setiap aksi agent dicatat** di tabel `agent_logs` (tool, argumen, hasil, durasi).
6. **Tidak ada data uji palsu.** Seed data untuk demo diberi flag `is_demo: true`.
7. Semua teks yang tampil ke pengguna dalam bahasa Indonesia sehari-hari.
8. Jangan membocorkan pesan error internal ke klien (`detail=str(e)` dilarang).

---

## 3. Arsitektur

```
ESP32 ──MQTT/TLS──► HiveMQ Cloud ──► [P2 mqtt_worker] ─┐
                                                        ├──► PostgreSQL
App Flutter ──REST/JWT──► [P1 api (FastAPI)] ───────────┤
      ▲                         │                       │
      └──── push (FCM) ── [P3 scheduler_worker] ────────┘
                                │
                           Groq LLM API
```

Satu codebase, tiga proses:

| Proses | Perintah | Tanggung jawab |
|---|---|---|
| P1 `api` | `uvicorn app.main:app` | REST API untuk aplikasi mobile |
| P2 `mqtt_worker` | `python -m app.workers.mqtt_worker` | Subscribe `potani/+/data` dan `potani/+/status`, validasi, simpan, tautkan ke sesi |
| P3 `scheduler_worker` | `python -m app.workers.scheduler_worker` | Pindai checkpoint jatuh tempo, kirim pengingat, jalankan langkah agent F6 |

P2 dan P3 harus berjalan terus-menerus, sehingga hosting serverless tidak cocok.

### 3.1 Struktur folder

```
app/
  main.py                 # FastAPI app, router, CORS
  config.py               # Settings dari env (pydantic-settings)
  db/                     # SQLAlchemy models, session, alembic
  api/routes/             # auth, devices, pots, sessions, measurements, recommend, evaluate, plans, checkpoints, chat
  domain/
    suitability.py        # Lapisan A: skor kesesuaian
    evaluator.py          # F3
    remediation.py        # F4
    crops/                # crops.json, remediation_rules.json
  agent/
    tools.py              # definisi + implementasi tool
    llm.py                # klien Groq, retry, timeout
    workflows/soil_first.py   # F2 workflow
    plan_validator.py
    loop_f6.py            # state machine closed-loop
    tanibot.py            # F7 ReAct loop
  workers/
    mqtt_worker.py
    scheduler_worker.py
  notify/fcm.py           # interface push notification
  ml/                     # eksperimen Lapisan B (tidak dipakai di jalur produksi P0)
tests/
```

### 3.2 Tech stack

| Bagian | Pilihan |
|---|---|
| Bahasa | Python 3.11+ |
| Web | FastAPI, Pydantic v2, pydantic-settings |
| Database | PostgreSQL, SQLAlchemy 2.x, Alembic |
| MQTT | paho-mqtt (TLS, port 8883) |
| Scheduler | APScheduler di P3 |
| LLM | SDK `groq`, model diatur lewat env `LLM_MODEL` |
| Auth | Firebase Auth (verifikasi ID token di server via firebase-admin) |
| Push | Firebase Cloud Messaging |
| ML eksperimen | scikit-learn, pandas, (opsional) TabPFN |
| Test | pytest, httpx |

Versi dikunci di `requirements.txt` atau `pyproject.toml` saat implementasi.

---

## 4. Model data

Semua tabel memakai `id UUID`, `created_at`, `updated_at`.

| Tabel | Kolom utama | Catatan |
|---|---|---|
| `users` | `firebase_uid`, `name`, `location_lat`, `location_lon`, `light_default`, `container_default`, `materials_owned[]` | Profil = memori jangka panjang |
| `devices` | `device_id` (UUID dari NVS ESP32), `owner_id`, `fw_version`, `last_seen_at`, `online` | Dipasangkan lewat endpoint pairing |
| `pots` | `owner_id`, `name`, `container_type`, `light_level` (`full`/`partial`/`shade`), `active` | |
| `plantings` | `pot_id`, `crop_id`, `source` (`recommender`/`user_choice`), `planted_at`, `status` (`planned`/`growing`/`harvested`/`failed`/`abandoned`), `outcome_note` | Label nyata untuk data loop |
| `sessions` | `device_id`, `pot_id`, `stage`, `opened_at`, `expires_at`, `measurement_id` | Jendela tautan MQTT, default 120 detik |
| `measurements` | `device_id`, `pot_id?`, `planting_id?`, `session_id?`, `stage`, `ph`, `moisture`, `temperature`, `soil_score`, `raw` (JSONB), `measured_at`, `client_measurement_id?`, `is_demo` | `pot_id` null = belum ditautkan |
| `plans` | `pot_id`, `planting_id?`, `kind` (`soil_first`/`remediation`), `goal`, `steps` (JSONB), `targets` (JSONB), `state`, `replan_count`, `version` | Lihat skema rencana 7.3 |
| `checkpoints` | `plan_id`, `pot_id`, `param`, `due_at`, `status` (`scheduled`/`reminded`/`measured`/`verified`/`missed`), `measurement_id?` | |
| `agent_logs` | `run_id`, `feature` (`F2`/`F6`/`F7`), `step`, `tool`, `args` (JSONB), `result` (JSONB), `latency_ms`, `llm_model`, `error?` | Untuk evaluasi RQ3 dan RQ5 |
| `chat_messages` | `user_id`, `pot_id?`, `role`, `content`, `tool_calls` (JSONB) | Memori jangka pendek TaniBot |

**Tahap (`stage`) yang valid:** `pre_plant`, `post_remediation`, `control`, `symptom`, `end`.

---

## 5. Ingest MQTT (P2)

### 5.1 Topik

| Topik | Arah | Retained | Isi |
|---|---|---|---|
| `potani/{device_id}/data` | perangkat → server | tidak | Hasil satu pengukuran |
| `potani/{device_id}/status` | perangkat → server | ya | `online`/`offline`, baterai, versi firmware |

### 5.2 Payload `data`

Field dari proposal (wajib): `device_id`, `soil_adc`, `moisture`, `status`, `ph_adc`, `ph_voltage`, `ph`, `temperature`, `soil_score`.
Field tambahan (opsional, bergantung PRD firmware): `measured_at` (epoch detik), `client_measurement_id` (UUID untuk dedupe data offline), `fw_version`, `stable` (bool).

### 5.3 Logika

1. Validasi skema dan rentang fisik: pH 0 sampai 14, moisture 0 sampai 100, suhu -10 sampai 60. Di luar rentang → simpan dengan `raw`, tandai `invalid`, jangan dipakai penilaian.
2. Dedupe: bila `client_measurement_id` sudah ada, abaikan.
3. Cari sesi terbuka untuk `device_id` (`expires_at > now`). Bila ada → isi `pot_id`, `stage`, `planting_id` aktif, tutup sesi. Bila tidak ada → simpan tanpa pot (`pot_id = null`).
4. Bila pengukuran menutup checkpoint yang jatuh tempo pada pot itu → tandai checkpoint `measured` dan picu langkah Verify F6 (lewat antrean tabel atau notifikasi ke P3).
5. Perbarui `devices.last_seen_at`.

**Kriteria terima:** pesan valid tersimpan dalam ≤ 2 detik setelah diterima; pesan duplikat tidak membuat baris baru; reconnect otomatis dengan backoff bila koneksi putus.

---

## 6. Mesin domain (deterministik)

### 6.1 Data tanaman `crops.json`

```json
{
  "cabai_rawit": {
    "display_name": "Cabai Rawit",
    "params": {
      "ph":          {"abs_min": null, "opt_min": null, "opt_max": null, "abs_max": null, "source": null},
      "moisture":    {"abs_min": null, "opt_min": null, "opt_max": null, "abs_max": null, "source": null},
      "temperature": {"abs_min": null, "opt_min": null, "opt_max": null, "abs_max": null, "source": null, "note": "suhu udara"}
    },
    "light": {"allowed": ["full", "partial"], "source": null}
  }
}
```

Nilai diisi tim dari FAO ECOCROP [1] dan Djaenudin dkk. [2]. **AI coding tidak boleh mengisi angka ini sendiri.** Untuk pengembangan, gunakan `crops.dev.json` berflag `is_demo` yang jelas terpisah.

### 6.2 Skor kesesuaian (Lapisan A)

Bentuk fungsi mengikuti EcoCrop [3]: nol di luar batas absolut, naik linier antara batas absolut dan optimal, bernilai 1 di dalam rentang optimal.

```
s(x) = 0                                   jika x < abs_min atau x > abs_max
s(x) = (x - abs_min) / (opt_min - abs_min) jika abs_min ≤ x < opt_min
s(x) = 1                                   jika opt_min ≤ x ≤ opt_max
s(x) = (abs_max - x) / (abs_max - opt_max) jika opt_max < x ≤ abs_max
```

- Parameter dengan `source = null` dilewati dan dicantumkan di `skipped_params`.
- Skor total = **minimum** skor parameter yang dinilai (faktor pembatas). Keputusan desain [O]: dipilih agar alasan mudah dijelaskan ("dibatasi oleh pH"); nilai perkalian juga disimpan untuk analisis.
- Cahaya: tanaman yang `light.allowed` tidak memuat `light_level` pot dikeluarkan dengan alasan.
- **Himpunan kandidat** = tanaman dengan skor total ≥ `SUITABILITY_THRESHOLD` (env, default 0.5 [O]), diurutkan menurun.
- **Toleransi sensor:** bila nilai berada dalam jarak ≤ MAE sensor dari batas rentang, tandai `near_boundary: true` (MAE dari env: pH 0.5, moisture 5, suhu 1, sesuai target proposal).

Output:

```json
{
  "candidates": [{"crop_id": "kangkung", "score": 0.82, "limiting_param": "ph", "per_param": {"ph": {"value": 6.1, "score": 0.82, "near_boundary": false}}, "reasons": ["pH sedikit di bawah rentang optimal"]}],
  "excluded": [{"crop_id": "tomat", "reason": "butuh cahaya penuh"}],
  "skipped_params": ["moisture"],
  "needs_remeasure": false
}
```

`needs_remeasure = true` bila lebih dari separuh tanaman berstatus `near_boundary` pada parameter pembatasnya.

### 6.3 Evaluator (F3)

Input `crop_id` + bacaan. Status per parameter: `ok`, `low`, `high`, `near_boundary`, `no_reference`. Verdict total: `cocok` (semua ok), `perlu_perbaikan` (ada low/high di dalam batas absolut), `tidak_cocok` (ada nilai di luar batas absolut). Bila ada `low`/`high`, sertakan `remediation_hint` dari 6.4.

### 6.4 Remediation (F4)

`remediation_rules.json`: per `param` × `condition` (`low`/`high`) berisi daftar opsi `{material, household: bool, dose, unit, per, source, notes}`. Fungsi `get_remediation(param, condition, materials_owned)` mengembalikan opsi rumah tangga lebih dulu, lalu pupuk komersial, dan hanya opsi yang punya `source`. Jeda verifikasi default: moisture 1 hari [O], pH 56 hari (berdasarkan perubahan pH oleh kapur yang mulai terlihat dalam beberapa bulan [4]); keduanya dapat diubah lewat env.

---

## 7. Agent

Definisi yang dipakai: agent = environment + tools + planning, bekerja dalam loop dengan feedback, dibantu memory [5]. Pembagian: F2 = workflow (jalur ditentukan kode), F6 dan F7 = agent [6].

### 7.1 Klien LLM

- SDK `groq`, model dari env `LLM_MODEL` (default `llama-3.1-8b-instant`; kandidat lain diuji di RQ3). Semua model Groq mendukung tool use; dukungan parallel tool use berbeda per model [7].
- `temperature` 0.2, timeout 20 detik, retry 2 kali untuk 429/5xx dengan backoff.
- Gagal → kembalikan `None`; pemanggil wajib punya fallback deterministik.

### 7.2 Tools

Didefinisikan dalam format function calling Groq (JSON Schema). Implementasi di `agent/tools.py`; setiap pemanggilan dicatat ke `agent_logs`.

| Tool | Jenis | Argumen | Output |
|---|---|---|---|
| `get_latest_reading` | read | `pot_id` | bacaan terbaru + umur data (jam) |
| `get_pot_history` | read | `pot_id`, `limit` | bacaan berlabel tahap, rencana, checkpoint, planting |
| `get_crop_requirements` | read | `crop_id` | rentang + sumber |
| `run_suitability` | read | `pot_id` atau `reading`, `light_level`, `container_type` | output 6.2 |
| `run_evaluator` | read | `crop_id`, `pot_id` | output 6.3 |
| `get_remediation` | read | `param`, `condition`, `materials_owned[]` | output 6.4 |
| `get_weather` | read | `lat`, `lon` | ringkasan prakiraan (P1: boleh stub dulu) |
| `ask_user` | read | `question`, `options[]` | jawaban pengguna (asinkron, lihat 7.6) |
| `create_plan` | write | rencana (skema 7.3) | `plan_id` atau error validasi |
| `update_plan` | write | `plan_id`, perubahan, `reason` | versi baru |
| `schedule_checkpoint` | write | `plan_id`, `param`, `due_in_days` | `checkpoint_id` |
| `send_reminder` | write | `user_id`, `title`, `body`, `deep_link` | status kirim |
| `log_outcome` | write | `planting_id`, `status`, `note` | ok |
| `escalate` | write | `pot_id`, `reason` | ok; tampilkan kartu "butuh bantuan" di aplikasi |

### 7.3 Skema rencana

```json
{
  "goal": "Menaikkan pH pot sebelum menanam cabai rawit",
  "crop_id": "cabai_rawit",
  "steps": [{"order": 1, "action": "Taburkan bahan X sesuai dosis", "remediation_ref": "ph.low.0"}],
  "targets": [{"param": "ph", "min": 6.0, "max": 6.8}],
  "checkpoints": [{"param": "ph", "due_in_days": 56}]
}
```

### 7.4 Validator rencana (deterministik)

Huyen menyarankan rencana divalidasi dengan heuristik sebelum dieksekusi, dan dibuat ulang bila buruk [5]. Aturan:

1. `crop_id` ada di himpunan kandidat terakhir, atau dipilih pengguna (plant-first).
2. Setiap langkah dengan bahan merujuk ke `remediation_ref` yang ada; dosis sama dengan yang di rules.
3. Target berada di dalam rentang optimal tanaman yang bersumber.
4. `due_in_days` ≥ jeda minimum parameter (6.4).
5. Maksimal 6 langkah.

Gagal → kirim pesan error ke LLM dan minta rencana ulang, maksimal 2 kali; bila tetap gagal, pakai rencana template deterministik.

### 7.5 F2 Soil-first workflow

Urutan tetap (bukan LLM yang memilih):

1. Ambil bacaan sesi `pre_plant`.
2. Bila konteks pot (cahaya, wadah) belum ada → `ask_user`.
3. `run_suitability`. Bila `needs_remeasure` → minta ukur ulang dan berhenti.
4. `get_weather` (opsional).
5. LLM menulis ringkasan kompromi untuk maksimal 3 kandidat teratas (hanya dari output tool).
6. Pengguna memilih tanaman → buat `planting` (`planned`).
7. Bila ada parameter di luar optimal → `get_remediation` → LLM menyusun rencana → validator → `create_plan` → `schedule_checkpoint`.

### 7.6 F6 Closed-loop (state machine)

State disimpan di `plans.state` karena satu putaran bisa berlangsung berminggu-minggu.

```
PLANNED ──user tandai "dikerjakan"──► AWAITING_CHECKPOINT
AWAITING_CHECKPOINT ──due_at lewat──► CHECKPOINT_DUE (kirim pengingat)
CHECKPOINT_DUE ──pengukuran masuk──► VERIFYING
CHECKPOINT_DUE ──3 hari tanpa ukur──► MISSED (pengingat ulang, maks 2)
VERIFYING ──target tercapai──► ACHIEVED (lanjut tahap: tanam / kontrol)
VERIFYING ──belum tercapai──► REPLANNING
REPLANNING ──rencana valid──► PLANNED (replan_count + 1)
REPLANNING ──replan_count ≥ 3──► ESCALATED
```

Langkah VERIFYING dan REPLANNING dijalankan agent: LLM boleh memanggil `get_latest_reading`, `get_pot_history`, `get_remediation`, `ask_user`, `run_suitability`, lalu harus berakhir dengan salah satu: `update_plan` + `schedule_checkpoint`, `log_outcome`, atau `escalate`. Batas 6 iterasi tool per langkah.

`ask_user` bersifat asinkron: agent menyimpan pertanyaan, state menunggu, dan jawaban dari aplikasi melanjutkan run yang sama.

### 7.7 F7 TaniBot

- Pola ReAct: LLM boleh memanggil semua tool read + `ask_user`; tool write hanya `send_reminder` dan `schedule_checkpoint` bila pengguna meminta eksplisit.
- Maksimal 5 iterasi tool per pesan.
- System prompt wajib memuat: aturan 1 dan 2; bila data kurang → minta ukur atau foto, jangan menebak diagnosis; jawaban maksimal 3 paragraf; cantumkan sumber panduan bila ada.
- Memori jangka pendek: 10 pesan terakhir dari `chat_messages`.

---

## 8. REST API (P1)

Semua endpoint kecuali `/health` butuh header `Authorization: Bearer <Firebase ID token>`. Respons error: `{"error": {"code": "...", "message": "pesan bahasa Indonesia"}}`.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/health` | Status API, DB, LLM |
| POST | `/devices/pair` | `{device_id}` → kaitkan perangkat ke pengguna |
| GET/POST | `/pots` | Daftar / buat pot |
| PATCH | `/pots/{id}` | Ubah cahaya, wadah, nama |
| POST | `/sessions` | `{device_id, pot_id, stage}` → buka jendela tautan 120 detik |
| GET | `/sessions/{id}` | Polling: apakah pengukuran sudah masuk |
| GET | `/measurements?pot_id=&unassigned=` | Riwayat / bacaan yang belum ditautkan |
| PATCH | `/measurements/{id}` | Tautkan bacaan yatim ke pot |
| POST | `/recommend` | F2: `{pot_id}` → output 6.2 + ringkasan LLM |
| POST | `/plantings` | Pilih tanaman → buat planting (+ rencana bila perlu) |
| POST | `/evaluate` | F3: `{pot_id, crop_id}` → output 6.3 |
| GET | `/plans/{id}` | Rencana + state |
| POST | `/plans/{id}/done` | Pengguna menandai langkah dikerjakan |
| POST | `/agent/answers` | Jawaban atas `ask_user` |
| GET | `/checkpoints?status=` | Daftar checkpoint |
| PATCH | `/plantings/{id}` | Catat hasil akhir (panen / gagal + catatan) |
| POST | `/chat` | F7: `{message, pot_id?}` → `{reply, tool_trace}` |
| GET | `/crops` | Daftar tanaman + rentang + sumber |

---

## 9. Scheduler (P3)

| Job | Interval | Aksi |
|---|---|---|
| `scan_due_checkpoints` | 15 menit | `scheduled` yang lewat `due_at` → `CHECKPOINT_DUE`, kirim pengingat |
| `scan_missed` | 1 jam | Belum diukur 3 hari → pengingat ulang, maks 2, lalu `missed` |
| `run_verify_queue` | 1 menit | Jalankan langkah VERIFYING untuk pengukuran checkpoint yang masuk |
| `expire_sessions` | 1 menit | Tutup sesi kedaluwarsa |
| `device_offline` | 10 menit | Tandai perangkat offline dari topik status |

Job harus idempoten (aman bila berjalan dua kali).

---

## 10. Kebutuhan non-fungsional

| Area | Ketentuan |
|---|---|
| Keamanan | CORS hanya domain yang ditentukan; kredensial di env; tidak ada secret di repo; pengguna hanya bisa mengakses pot dan perangkat miliknya |
| Validasi | Semua input lewat Pydantic dengan batas `ge`/`le`; panjang pesan chat ≤ 500 karakter |
| Kinerja | `/evaluate` dan `/recommend` tanpa LLM ≤ 300 ms; dengan LLM ≤ 8 detik (p95) |
| Ketahanan | Endpoint inti tetap berjalan bila LLM atau FCM gagal |
| Observabilitas | Log terstruktur JSON; `agent_logs` untuk setiap tool call |
| Data | Migrasi lewat Alembic; seed demo diberi `is_demo` |

---

## 11. Pengujian dan kriteria terima

| Modul | Uji minimum |
|---|---|
| `suitability.py` | Unit test fungsi s(x) di 5 titik (di luar absolut, tepi absolut, tengah lereng, tepi optimal, dalam optimal); parameter tanpa sumber dilewati; filter cahaya |
| `evaluator.py` | Verdict untuk kombinasi ok/low/high/out-of-absolute |
| `plan_validator.py` | Menolak dosis yang tidak sama dengan rules, target di luar optimal, jeda terlalu pendek |
| `mqtt_worker` | Payload valid, invalid, duplikat, dengan dan tanpa sesi |
| F6 state machine | Uji transisi semua state termasuk `ESCALATED` pada replan ketiga |
| Tanpa LLM | Set `LLM_MODEL` salah → semua endpoint inti tetap 200 dengan `narrative: null` |

**Hook evaluasi riset:**

- RQ1: skrip `ml/noise_stability.py` mengukur persentase perubahan kandidat teratas saat input diberi noise sebesar MAE sensor (bandingkan dengan baseline KNN 76% [T]).
- RQ3: `tests/agent_eval/scenarios.jsonl` berisi minimal 50 skenario `{input, expected_tools, expected_args}`; skrip menghitung akurasi pemilihan tool dan argumen per model.
- RQ5: query dari `plans`, `checkpoints`, `agent_logs` untuk proporsi checkpoint yang mencapai target dan jumlah revisi per kasus.

---

## 12. Prioritas

| Prioritas | Isi | Target |
|---|---|---|
| P0 | DB + migrasi, auth, pairing, sesi, mqtt_worker, suitability, evaluator, remediation, `/recommend`, `/evaluate`, `/crops`, TaniBot dengan 4 tool read | Sebelum final 10 November 2026 |
| P1 | F2 rencana + validator, checkpoint, scheduler, F6 state machine, FCM | Demo alur merawat (boleh dengan waktu dipercepat untuk simulasi) |
| P2 | `get_weather` asli, eksperimen Lapisan B dan C, skrip RQ3 lengkap | Setelah final |

Untuk demo, sediakan env `TIME_ACCELERATION` agar jeda checkpoint (misalnya 56 hari) bisa disimulasikan dalam menit, dan tandai jelas di UI bahwa ini mode demo.

---

## 13. Pertanyaan terbuka

1. Sumber rentang kelembapan tanah per tanaman (tidak ada di ECOCROP) dan pemetaan nilai SEN0193 ke kadar air tanah lewat kalibrasi gravimetri.
2. Asal-usul dataset P2L di repo (tautan Kaggle dan definisi kolom `humidity`).
3. Model Groq final (hasil RQ3).
4. Auth: Firebase Auth atau alternatif lain, disesuaikan dengan PRD mobile.
5. Hosting yang mampu menjalankan 3 proses terus-menerus dalam batas biaya tim.

---

## Rujukan

1. FAO Land and Water Development Division, "ECOCROP database." https://ecocrop.apps.fao.org/ecocrop/srv/en/home
2. D. Djaenudin, Marwan H., Subagjo H., dan A. Hidayat, *Petunjuk Teknis Evaluasi Lahan untuk Komoditas Pertanian*, ed. 1. Bogor: Balai Penelitian Tanah, 2003.
3. J. Ramirez-Villegas, A. Jarvis, dan P. Läderach, "Empirical approaches for assessing impacts of climate change on agriculture: The EcoCrop model and a case study with grain sorghum," *Agric. For. Meteorol.*, vol. 170, pp. 67–78, 2013, doi: 10.1016/j.agrformet.2011.09.005.
4. NC Department of Agriculture and Consumer Services, "Agronomic Services: soil testing FAQs." https://www.ncagr.gov/agronomic-services-soil-testing-faqs
5. C. Huyen, "Agents," Jan. 2025, diadaptasi dari *AI Engineering* (O'Reilly, 2025). https://huyenchip.com/2025/01/07/agents
6. Anthropic, "Building effective agents," Des. 2024. https://www.anthropic.com/research/building-effective-agents
7. Groq, "Tool use," GroqCloud documentation (diakses 29 Okt. 2026). https://console.groq.com/docs/tool-use
