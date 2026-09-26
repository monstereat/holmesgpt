"""Validate worker dependencies before Celery begins consuming tasks."""

import sys

from worker import celery_app, validate_worker_configuration


def main() -> None:
    validate_worker_configuration()
    celery_app.worker_main(sys.argv[1:])


if __name__ == "__main__":
    main()
