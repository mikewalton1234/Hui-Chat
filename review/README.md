# Hui Chat continuous multi-agent review

This system reviews Hui Chat through four independent domains:

1. **Correctness** — Python/JavaScript syntax, dependency hygiene, version drift, and feature regression doctors.
2. **Security** — embedded-secret detection, realtime authentication, file authorization, moderation, room membership, and RBAC.
3. **Deployment** — Gunicorn/systemd/Redis topology, packaging assumptions, admin branding consistency, and an optional live service smoke test.
4. **UI behavior** — missed-message click-to-open behavior, presence, group roster/dock rendering, and other stable frontend regression doctors.

The four deterministic agents run concurrently. When `OPENAI_API_KEY` is present, the runner also uses GPT-5.6 Multi-agent beta so a root reviewer can delegate the same four review domains to parallel subagents and reconcile their evidence.

## Local use

From the Hui Chat project root:

```bash
python -m venv .venv-review
source .venv-review/bin/activate
python -m pip install -r requirements-review.txt

# Deterministic review only
python tools/hui_multi_agent_review.py --no-ai --mode fast

# Full deterministic scans plus GPT-5.6 multi-agent review
export OPENAI_API_KEY="your API key"
python tools/hui_multi_agent_review.py --mode full
```

Reports are written to:

- `review_reports/multi-agent-review.md`
- `review_reports/multi-agent-review.json`

## Reviewing a particular change

```bash
python tools/hui_multi_agent_review.py --base origin/main --mode full
```

When Git metadata exists, only the sanitized diff is sent to GPT-5.6. In an extracted source archive without Git metadata, the runner creates a size-limited sanitized snapshot.

## Live service check

When Hui Chat is already running:

```bash
python tools/hui_multi_agent_review.py \
  --no-ai \
  --live-url http://127.0.0.1:5000
```

The live smoke test is assigned to the deployment agent. It checks public routes and expected authentication redirects without modifying data.

## GitHub Actions

The included `.github/workflows/hui-multi-agent-review.yml` runs on pull requests, pushes to the primary branches, manual dispatch, and a weekly deep review.

Add this repository secret to enable GPT-5.6 analysis:

- `OPENAI_API_KEY`

Optional repository variables:

- `HUI_REVIEW_MODEL` — defaults to `gpt-5.6-sol`; use `gpt-5.6-terra` to reduce cost.
- `HUI_REVIEW_LIVE_URL` — URL of a safe test/staging instance for route smoke checks.

Pull requests from forks do not receive repository secrets, so they still get all deterministic checks while the GPT review is skipped.

## Gate policy

- **FAIL** — a deterministic required check fails or GPT-5.6 reports a confirmed HIGH/CRITICAL defect.
- **WARN** — an optional scanner is unavailable, a medium-risk issue exists, or GPT analysis is skipped.
- **PASS** — required local checks pass and no material AI finding is reported.

By default, CI exits nonzero only for `FAIL`. Use `--fail-on warn` for a stricter branch-protection policy.

## Privacy and safety

The runner excludes local configuration, `.env` variants, keys, certificates, databases, archives, media assets, uploads, logs, backups, and generated reports from model context. It performs defensive review only and instructs the model not to follow instructions embedded in repository content.
