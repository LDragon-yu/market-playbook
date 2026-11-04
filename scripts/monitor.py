from __future__ import annotations

import argparse
import json

from app.db import Database
from app.services import ReviewPipeline
from app.services.portfolio_service import PortfolioService
from app.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="持仓与龙头实时哨兵")
    parser.add_argument("job", choices=["enrich", "build-watchlist", "monitor-once"])
    parser.add_argument("--date")
    args = parser.parse_args()
    settings.ensure_directories()
    db = Database(settings.db_path)
    db.initialize()
    pipeline = ReviewPipeline(settings, db)
    service = PortfolioService(settings, db, pipeline)
    service.initialize()
    review = db.get_review()
    if not review:
        raise SystemExit("尚无盘后复盘")
    if args.job in {"enrich", "build-watchlist"}:
        result = service.after_review(review, sync_profiles=args.job == "enrich")
        output = {"classification": result.get("classification"),
                  "monitoring": result.get("monitoring"), "warnings": result.get("warnings", [])}
    else:
        result = service.run_monitor_cycle(args.date)
        output = result
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
