"""Play Store viability spike — permanent smoke check for google-play-scraper.

Run with:
    uv run --with google-play-scraper python scripts/playstore_spike.py

Or (if already pinned in pyproject.toml):
    uv run python scripts/playstore_spike.py

Exit code 0 = all checks passed.
Exit code 1 = one or more checks failed (see output for details).
"""
import sys
from datetime import datetime

WELL_KNOWN_APPS = [
    "com.duolingo",
    "com.spotify.music",
    "com.notion.so",
]

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def _result(ok: bool, label: str, detail: str = "") -> bool:
    icon = PASS if ok else FAIL
    print(f"  {icon}  {label}" + (f": {detail}" if detail else ""))
    return ok


def check_app_metadata() -> bool:
    from google_play_scraper import app as gps_app

    print("\n[1] app() — fetch metadata for well-known apps")
    all_ok = True
    for app_id in WELL_KNOWN_APPS:
        try:
            result = gps_app(app_id, lang="en", country="us")
            title = result.get("title", "")
            desc = result.get("description", "")
            ok = bool(title and desc)
            _result(ok, app_id, f"title={title!r:.40}...")
            all_ok = all_ok and ok
        except Exception as exc:
            _result(False, app_id, f"EXCEPTION: {exc}")
            all_ok = False
    return all_ok


def check_reviews() -> bool:
    from google_play_scraper import Sort, reviews as gps_reviews

    print("\n[2] reviews() — fetch 50 newest reviews per app")
    all_ok = True
    for app_id in WELL_KNOWN_APPS:
        try:
            result, _ = gps_reviews(
                app_id,
                sort=Sort.NEWEST,
                count=50,
                lang="en",
                country="us",
            )
            ok_count = len(result) >= 10
            if result:
                sample = result[0]
                print(f"  {WARN}  response keys for {app_id}: {list(sample.keys())}")
                ok_fields = all(
                    k in sample
                    for k in ("content", "at", "score")
                )
                ok = ok_count and ok_fields
                at_type = type(sample.get("at"))
                _result(
                    ok,
                    app_id,
                    f"count={len(result)}, keys={list(sample.keys())}, at_type={at_type.__name__}",
                )
            else:
                _result(False, app_id, "empty result")
                ok = False
            all_ok = all_ok and ok
        except Exception as exc:
            _result(False, app_id, f"EXCEPTION: {exc}")
            all_ok = False
    return all_ok


def check_list() -> bool:
    print("\n[3] list() — top-20 HEALTH_AND_FITNESS free apps")
    try:
        # Try with string category first (enums have been historically unstable)
        from google_play_scraper import list as gps_list

        result = gps_list(
            collection="TOP_FREE",
            category="HEALTH_AND_FITNESS",
            country="us",
            num=20,
            lang="en",
        )
        ok = len(result) >= 10 and all("appId" in a and "title" in a for a in result[:5])
        _result(ok, "HEALTH_AND_FITNESS/TOP_FREE", f"count={len(result)}")
        return ok
    except AttributeError:
        # list() may not exist in this version; try Collection/Category enums
        try:
            from google_play_scraper import list as gps_list
            from google_play_scraper.constants.google_play import Category, Collection

            result = gps_list(
                collection=Collection.TOP_FREE,
                category=Category.HEALTH_AND_FITNESS,
                country="us",
                num=20,
                lang="en",
            )
            ok = len(result) >= 10
            _result(ok, "HEALTH_AND_FITNESS/TOP_FREE (enum)", f"count={len(result)}")
            return ok
        except Exception as exc:
            _result(False, "list() with enums", f"EXCEPTION: {exc}")
            return False
    except Exception as exc:
        _result(False, "list()", f"EXCEPTION: {exc}")
        return False


def main() -> int:
    try:
        import google_play_scraper  # noqa: F401
        import google_play_scraper as _gps  # noqa: F401
        version = getattr(_gps, "__version__", "unknown")
    except ImportError as exc:
        print(f"{FAIL}  google-play-scraper not installed: {exc}")
        print("Run: uv run --with google-play-scraper python scripts/playstore_spike.py")
        return 1

    print(f"google-play-scraper version: {version}")
    print(f"Spike run at: {datetime.utcnow().isoformat()}Z")

    results = {
        "app_metadata": check_app_metadata(),
        "reviews": check_reviews(),
        "list": check_list(),
    }

    print("\n=== SUMMARY ===")
    for check, ok in results.items():
        icon = PASS if ok else FAIL
        print(f"  {icon}  {check}")

    if all(results.values()):
        print("\nAll checks PASSED. Proceed with C-01: pin 1.2.7 (or current version).")
        return 0
    elif results["reviews"] and not results["list"]:
        print("\nreviews() works, list() BROKEN → use static seed (path b from C-00).")
        return 0
    else:
        print("\nOne or more critical checks FAILED. See C-00 decision table.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
