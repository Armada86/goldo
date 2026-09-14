"""One-shot poll, for running as a scheduled GitHub Actions job instead of
main.py's always-on BlockingScheduler (no server needed)."""

from main import poll_once
from storage import init_db

if __name__ == "__main__":
    init_db()
    poll_once()
