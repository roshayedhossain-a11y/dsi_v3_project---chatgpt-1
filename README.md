# DSI Global Remote Engineering Hiring Signal Collector V3

This repo collects hiring intent data for DS Innovators.

It does not find emails, does not find decision makers, and does not send outreach. It only collects job posts and classifies whether they match the DSI ICP.

## What V3 fixes

V3 fixes the big V2 problems:

1. Unknown location cannot enter strict ICP.
2. Weak remote cannot enter strict ICP unless full page text proves global remote.
3. WeWorkRemotely is not treated as automatically worldwide.
4. Source errors are logged instead of hidden.
5. Big companies over 500 employees are removed from strict ICP.
6. Headcount bucket is required for strict ICP.
7. Duplicates are removed using URL, ATS ID, company domain, role family, and fuzzy title matching.
8. Strict, secondary, verification, rejected, company summary, and source report files are separated.

## Files

1. `dsi_scraper_v3.py`
2. `sources.yml`
3. `requirements.txt`
4. `.github/workflows/daily_scrape_v3.yml`
5. `tests/test_filters.py`

## Output files

The script creates these files inside `output/`:

1. `dsi_v3_strict_icp_DATE.csv`
2. `dsi_v3_secondary_DATE.csv`
3. `dsi_v3_needs_verification_DATE.csv`
4. `dsi_v3_rejected_DATE.csv`
5. `dsi_v3_company_summary_DATE.csv`
6. `dsi_v3_source_report_DATE.csv`

It also creates a JSON run log inside `logs/`.

## Strict ICP rules

A job only enters strict ICP if it satisfies all of this:

1. Headcount bucket is 10 to 50, 51 to 100, or 101 to 200.
2. Role is a core DSI engineering role.
3. Job is fresh within 21 days.
4. Remote worldwide is proven.
5. No country restriction is found.
6. No work authorization restriction is found.
7. Job URL exists.
8. Company is not an agency, recruiter, anonymous company, or staffing firm.
9. Score is at least 80.
10. It is not a duplicate.

## Secondary rules

Secondary contains good but imperfect rows, for example:

1. Headcount is 201 to 500.
2. Global remote looks strong but score is below strict.
3. Source is trusted but needs a human check.

## Needs verification rules

Needs verification contains jobs that may be useful but are not proven enough. This includes weak remote jobs where no country restriction was found but global remote was not proven.

## Rejected rules

Rejected contains jobs rejected for country restriction, agency, non engineering role, stale job, duplicate, unknown remote status, headcount over 500, or missing URL.

## Setup on GitHub

1. Create a private GitHub repo.
2. Upload all files from this package.
3. Make sure `.github/workflows/daily_scrape_v3.yml` is in the repo exactly at that path.
4. Go to the Actions tab.
5. Open `DSI Global Remote Jobs V3`.
6. Click `Run workflow`.
7. Download the artifact after it finishes.

The workflow runs daily at 06:00 UTC, which is 12:00 PM Bangladesh time.

## Local run

```bash
pip install -r requirements.txt
pytest -q
python dsi_scraper_v3.py
```

For a quick test without checking every source:

```bash
python dsi_scraper_v3.py --limit-sources 5
```

To skip full page checks during debugging:

```bash
python dsi_scraper_v3.py --no-verify-pages
```

Do not use `--no-verify-pages` for final production data.

## How to add more sources

Edit `sources.yml`.

Add more companies using these types:

1. `greenhouse`
2. `lever`
3. `ashby`
4. `smartrecruiters`
5. `rss`
6. `generic_html`

Best source to add:

Small remote first SaaS companies using official ATS career pages.

Bad source to add:

Random job boards, agencies, recruiter posts, anonymous jobs, scraped reposts, or giant enterprise companies.

## Important reality check

A free scraper cannot magically guarantee 500 perfect strict ICP posts every day. V3 does not fake the count. It gives the real count and tells you where the gaps are.

To scale to 500 strong rows, expand `sources.yml` with many more 10 to 200 employee remote first SaaS companies.
