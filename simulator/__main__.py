"""Command-line interface.

python -m simulator scenarios
python -m simulator generate --hours 6 --incident payment_gateway_timeout:45:30 --out simulator/output
python -m simulator tail --incident payment_gateway_timeout:10:30 --service payment-service --level WARN
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from simulator.engine import ScheduledIncident, simulate
from simulator.incidents.scenarios import SCENARIOS

LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]


def parse_incident(value: str, now: datetime) -> ScheduledIncident:
    """`<scenario>:<started N minutes ago>:<duration minutes>`."""
    try:
        key, ago, duration = value.split(":")
        return ScheduledIncident(key, now - timedelta(minutes=int(ago)), timedelta(minutes=int(duration)))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected <scenario>:<minutes ago>:<duration>, got {value!r}") from exc


def main(argv: list[str] | None = None) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    parser = argparse.ArgumentParser(prog="simulator", description="ShopStream environment simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scenarios", help="list incident scenarios")
    for name in ("generate", "tail"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--hours", type=float, default=1.0, help="length of the simulated window ending now")
        cmd.add_argument("--seed", type=int, default=42)
        cmd.add_argument("--incident", action="append", default=[], type=lambda v: parse_incident(v, now))
    sub.choices["generate"].add_argument("--out", type=Path, default=Path("simulator/output"))
    sub.choices["tail"].add_argument("--service")
    sub.choices["tail"].add_argument("--level", choices=LEVELS, default="INFO")
    sub.choices["tail"].add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)

    if args.command == "scenarios":
        for s in SCENARIOS.values():
            print(f"{s.key:<26} {s.severity}  {s.symptom_service:<22} {s.title}")
        return

    result = simulate(now - timedelta(hours=args.hours), now, args.incident, seed=args.seed)

    if args.command == "generate":
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / "logs.jsonl").open("w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(log.to_dict()) + "\n" for log in result.logs)
        with (args.out / "metrics.jsonl").open("w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(m.to_dict()) + "\n" for m in result.metrics)
        print(f"wrote {len(result.logs)} log lines and {len(result.metrics)} metric samples to {args.out}")
        return

    min_level = LEVELS.index(args.level)
    lines = [
        log
        for log in result.logs
        if LEVELS.index(log.level) >= min_level and (args.service is None or log.service == args.service)
    ]
    for log in lines[-args.limit :]:
        print(log.format())


if __name__ == "__main__":
    main()
