from __future__ import annotations

import argparse
import json

from app.db import Database
from app.services import ReviewPipeline
from app.services.portfolio_service import PortfolioService
from app.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="A股复盘定时任务")
    parser.add_argument("job", choices=["seed-demo", "post-market", "pre-market"])
    parser.add_argument("--date")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已冻结或历史的盘前预案（旧版本会归档）")
    args = parser.parse_args()
    settings.ensure_directories()
    db = Database(settings.db_path)
    db.initialize()
    pipeline = ReviewPipeline(settings, db)
    portfolio = PortfolioService(settings, db, pipeline)
    portfolio.initialize()
    if args.job == "seed-demo":
        result = pipeline.seed_demo(force=True)
    elif args.job == "post-market":
        review = pipeline.run_post_market(args.date, demo=args.demo)
        result = portfolio.after_review(review, sync_profiles=not args.demo)
    else:
        plan = pipeline.run_pre_market(args.date, demo=args.demo, force=args.force)
        result = portfolio.after_plan(plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
