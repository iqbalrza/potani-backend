"""Seed data demo (semua baris diberi is_demo=True). Jalankan: python -m app.seed_demo --uid <firebase_uid>"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db.models import Device, Pot, User
from app.db.session import session_scope


def seed(uid: str, device_id: str) -> dict:
    with session_scope() as db:
        user = db.scalars(select(User).where(User.firebase_uid == uid)).first()
        if user is None:
            user = User(firebase_uid=uid, name="Pengguna Demo", materials_owned=["abu kayu"], is_demo=True)
            db.add(user)
            db.flush()
        dev = db.scalars(select(Device).where(Device.device_id == device_id)).first()
        if dev is None:
            dev = Device(device_id=device_id, owner_id=user.id)
            db.add(dev)
        elif dev.owner_id is None:
            dev.owner_id = user.id
        pot = db.scalars(select(Pot).where(Pot.owner_id == user.id, Pot.is_demo.is_(True))).first()
        if pot is None:
            pot = Pot(owner_id=user.id, name="Pot Demo", light_level="full", container_type="pot_sedang",
                      is_demo=True)
            db.add(pot)
        db.flush()
        return {"user_id": str(user.id), "firebase_uid": uid, "device_id": device_id, "pot_id": str(pot.id)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uid", default="demo", help="firebase_uid (mode dev: token 'dev:<uid>')")
    ap.add_argument("--device", default="potani-demo-01")
    args = ap.parse_args()
    print(seed(args.uid, args.device))


if __name__ == "__main__":
    main()
