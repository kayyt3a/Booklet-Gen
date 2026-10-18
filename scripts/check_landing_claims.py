from pathlib import Path


LANDING_TEMPLATE = Path(__file__).resolve().parents[1] / "booklet_gen" / "webapp" / "templates" / "landing.html"
CLAIM_ASSERTIONS = {
    "Each booklet is generated fresh for the year level and topic you choose.":
        "two students never get the same page",
}


def main() -> int:
    landing = LANDING_TEMPLATE.read_text(encoding="utf-8")
    failures = []
    for supported, forbidden in CLAIM_ASSERTIONS.items():
        if supported not in landing:
            failures.append(f"missing supported wording: {supported}")
        if forbidden in landing:
            failures.append(f"forbidden wording returned: {forbidden}")

    if failures:
        print("Landing claims check failed.")
        for failure in failures:
            print(f"  FAIL: {failure}")
        return 1

    print("Landing claims check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
