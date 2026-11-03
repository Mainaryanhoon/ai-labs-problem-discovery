"""python -m app.leads run|report|accept|reject|list"""
from __future__ import annotations

import argparse
import json
import sys

from app.db import SessionLocal, initialize_database


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.leads")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--max-calls", type=int, default=40)
    sub.add_parser("report")
    for name in ("accept", "reject"):
        a = sub.add_parser(name); a.add_argument("lead_id", type=int)
        a.add_argument("--reason", required=True); a.add_argument("--reviewer", default="reviewer")
    sub.add_parser("list")
    args = p.parse_args(argv)

    from app.leads import models  # noqa: F401
    from app.leads import service
    from app.leads.models import Lead
    from app.leads.report import write_report

    initialize_database()
    db = SessionLocal()
    try:
        if args.cmd == "run":
            from app.leads.llm import complete_json
            f = service.run_extraction(db, complete_json, max_calls=args.max_calls)
            print(json.dumps(f, indent=2)); return 0
        if args.cmd == "report":
            path, *_ = write_report(db, None)
            print(path)
            return 0
        if args.cmd in ("accept", "reject"):
            decision = "accepted" if args.cmd == "accept" else "rejected"
            try:
                service.record_feedback(db, args.lead_id, decision, args.reason, args.reviewer)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr); return 2
            print(f"lead {args.lead_id} {decision}"); return 0
        if args.cmd == "list":
            for l in db.query(Lead).order_by(Lead.score.desc()).all():
                print(l.id, l.status, l.score, l.delivery_type, l.title)
            return 0
    finally:
        db.close()
    return 0
