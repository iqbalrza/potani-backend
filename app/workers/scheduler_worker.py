"""P3: scheduler. Jalankan: python -m app.workers.scheduler_worker

Semua job idempoten: perubahan status membuat eksekusi ganda tidak berefek.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent import loop_f6
from app.config import get_settings
from app.db.models import Device, Session as LinkSession
from app.db.session import session_scope
from app.db.types import utcnow
from app.logging_setup import setup_logging

log = logging.getLogger("potani.scheduler")


def expire_sessions(db: Session) -> int:
    now = utcnow()
    res = db.execute(update(LinkSession).where(LinkSession.closed_at.is_(None), LinkSession.expires_at <= now)
                     .values(closed_at=now))
    db.commit()
    return res.rowcount or 0


def device_offline(db: Session) -> int:
    cutoff = utcnow() - timedelta(minutes=get_settings().device_offline_minutes)
    ids = db.scalars(select(Device.id).where(Device.online.is_(True), Device.last_seen_at < cutoff)).all()
    if ids:
        db.execute(update(Device).where(Device.id.in_(ids)).values(online=False))
    db.commit()
    return len(ids)


JOBS = {
    # nama: (fungsi, interval nyata dalam detik, ikut dipercepat TIME_ACCELERATION?)
    "scan_due_checkpoints": (loop_f6.scan_due_checkpoints, 15 * 60, True),
    "scan_missed": (loop_f6.scan_missed, 60 * 60, True),
    "run_verify_queue": (loop_f6.run_verify_queue, 60, False),
    "expire_sessions": (expire_sessions, 60, False),
    "device_offline": (device_offline, 10 * 60, False),
}


def run_job(name: str) -> None:
    fn = JOBS[name][0]
    try:
        with session_scope() as db:
            n = fn(db)
        if n:
            log.info("job_done", extra={"job": name, "processed": n})
    except Exception:  # noqa: BLE001
        log.exception("job_failed", extra={"job": name})


def interval_for(name: str) -> float:
    seconds, accelerated = JOBS[name][1], JOBS[name][2]
    accel = get_settings().time_acceleration
    return max(10.0, seconds / accel) if accelerated and accel > 1 else float(seconds)


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    if s.demo_mode:
        log.warning("demo_time_acceleration", extra={"factor": s.time_acceleration})
    scheduler = BlockingScheduler(timezone="UTC")
    for name in JOBS:
        scheduler.add_job(run_job, "interval", args=[name], seconds=interval_for(name), id=name,
                          max_instances=1, coalesce=True, next_run_time=utcnow())
    log.info("scheduler_starting", extra={"jobs": {n: interval_for(n) for n in JOBS}})
    scheduler.start()


if __name__ == "__main__":
    main()
