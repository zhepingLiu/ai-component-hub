from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path


logger = logging.getLogger("orchestrator.staging_cleanup")


def cleanup_staging_dir(staging_dir: str, retention_sec: int) -> dict[str, int]:
    base = Path(staging_dir)
    now = time.time()
    removed_dirs = 0
    removed_files = 0
    skipped_entries = 0

    if retention_sec <= 0:
        raise ValueError("retention_sec must be greater than 0")

    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return {"removed_dirs": 0, "removed_files": 0, "skipped_entries": 0}

    for entry in base.iterdir():
        try:
            age_sec = now - entry.stat().st_mtime
            if age_sec < retention_sec:
                continue

            if entry.is_dir():
                removed_files += sum(1 for child in entry.rglob("*") if child.is_file())
                shutil.rmtree(entry)
                removed_dirs += 1
            elif entry.is_file():
                entry.unlink()
                removed_files += 1
            else:
                skipped_entries += 1
        except FileNotFoundError:
            continue
        except Exception:
            skipped_entries += 1
            logger.exception(
                {
                    "event": "staging.cleanup_entry_failed",
                    "path": str(entry),
                }
            )

    return {
        "removed_dirs": removed_dirs,
        "removed_files": removed_files,
        "skipped_entries": skipped_entries,
    }


async def run_staging_cleanup_loop(
    *,
    staging_dir: str,
    retention_sec: int,
    interval_sec: int,
    stop_event: asyncio.Event,
) -> None:
    if interval_sec <= 0:
        raise ValueError("interval_sec must be greater than 0")

    Path(staging_dir).mkdir(parents=True, exist_ok=True)

    while not stop_event.is_set():
        try:
            stats = cleanup_staging_dir(staging_dir, retention_sec)
            if any(stats.values()):
                logger.info(
                    {
                        "event": "staging.cleanup_completed",
                        "staging_dir": staging_dir,
                        "retention_sec": retention_sec,
                        "interval_sec": interval_sec,
                        **stats,
                    }
                )
        except Exception as exc:
            logger.exception(
                {
                    "event": "staging.cleanup_failed",
                    "staging_dir": staging_dir,
                    "retention_sec": retention_sec,
                    "interval_sec": interval_sec,
                    "error": str(exc),
                }
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            continue
