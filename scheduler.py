"""In-code scheduler — no crontab, no macOS Full Disk Access needed.

Runs the full scan (grade + scan + desktop notification + dashboard, via cron_run.sh)
at the IST slots every weekday, and KEEPS RUNNING across days. Start it once and
leave it:

    ./run.sh auto        (or: python scheduler.py)

The bot self-skips weekends/holidays, so only real trading days produce a
notification. Times are configurable via RUN_TIMES_IST (default 09:12,10:15,13:15).
On first start it will NOT back-fire a slot that already passed today.
"""
import os
import sys
import time
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
TZ = ZoneInfo(os.getenv("MARKET_TIMEZONE", "Asia/Kolkata"))
HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "cron_run.sh")


def main():
    times = sorted(t.strip() for t in os.getenv("RUN_TIMES_IST", "09:12,10:15,13:15").split(",") if t.strip())
    now = datetime.now(TZ)
    done_day = now.date()
    done = {t for t in times if now.strftime("%H:%M") >= t}  # don't back-fire passed slots today
    print(f"[{now:%Y-%m-%d %H:%M IST}] Scheduler up. Runs {times} IST on weekdays, "
          f"stays running across days. Ctrl-C to stop.", flush=True)
    while True:
        now = datetime.now(TZ)
        if now.date() != done_day:        # new day -> reset
            done_day, done = now.date(), set()
        if now.weekday() < 5:             # Mon-Fri
            hhmm = now.strftime("%H:%M")
            for t in times:
                if t not in done and hhmm >= t:
                    print(f"[{now:%Y-%m-%d %H:%M IST}] running {t} slot…", flush=True)
                    subprocess.run(["/bin/bash", RUNNER], cwd=HERE)
                    done.add(t)
        time.sleep(30)


if __name__ == "__main__":
    main()
