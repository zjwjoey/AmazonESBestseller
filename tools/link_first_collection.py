"""Explicit non-default entry point for reviewed URL-first fresh collection."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amazon_es_bestseller.orchestration.link_first import LinkFirstRunner  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--continue-after-sample", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--exclude-current-details", type=Path, nargs="+",
                        help="Current-batch candidate JSONs used only to exclude already collected identities")
    parser.add_argument("--migrate-delivery-preflight", action="store_true",
                        help="Audited code migration only for an empty run stopped by Spain preflight")
    parser.add_argument("--migrate-delivery-auto", action="store_true")
    parser.add_argument("--migrate-delivery-auto-finish", action="store_true")
    parser.add_argument("--auto-spain-delivery", action="store_true",
                        help="Authorize one ordinary delivery popover setup on the current page")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    # Host-local OS lock prevents concurrent navigation or duplicate dispatch.
    with (args.out_dir / "process.lock").open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner_class, options = LinkFirstRunner, {}
        if args.exclude_current_details:
            from amazon_es_bestseller.monitoring.link_first_coverage import CoverageRunner
            runner_class = CoverageRunner
            options["exclude_current_details"] = args.exclude_current_details
        runner = runner_class(args.plan, args.out_dir, ROOT, sample_size=args.sample_size,
                                 continue_after_sample=args.continue_after_sample, resume=args.resume,
                                 migrate_delivery_preflight=args.migrate_delivery_preflight,
                                 migrate_delivery_auto=args.migrate_delivery_auto,
                                 migrate_delivery_auto_finish=args.migrate_delivery_auto_finish, **options)
        if args.prepare_only:
            runner.save("PREPARED")
            print("PREPARED; no browser or network started", flush=True)
            return
        try:
            print(runner.run(profile_dir=args.profile_dir, auto_spain_delivery=args.auto_spain_delivery), flush=True)
        except Exception as exc:
            runner.save("ERROR", error=str(exc))
            raise


if __name__ == "__main__":
    main()
