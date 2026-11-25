"""Run from Backend: python -m app.worker [--once]."""
import argparse
import logging
import time
# Import registered models/routes before configuring ORM relationships.
from app.main import app
from app.services.processing_service import run_next_job
from database.connection import engine


def main():
    parser = argparse.ArgumentParser(description="UniKnowledge document-processing worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        while True:
            processed = run_next_job(engine)
            if args.once:
                return
            if not processed:
                time.sleep(2)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
