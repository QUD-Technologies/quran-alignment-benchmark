# Leaderboard service

Svelte/TypeScript/Vite frontend and FastAPI service. The service uses `qab` for all scores, including arbitrary recording subsets. No audio column is loaded: PyArrow reads selected columns of the versioned dataset Parquet using HTTP ranges.

## Run locally

```powershell
pip install -e ".[dev]" -r leaderboard/requirements.txt
cd leaderboard/frontend
npm ci
npm run build
cd ../..
$env:QAB_STORE_DIR = 'build/leaderboard-store'
python -m uvicorn leaderboard.app:app --port 8037
```

For frontend development, `npm run dev` proxies API requests to port 8037. Local browsing and previews work without HF credentials. There is deliberately no local authentication bypass. OAuth publishing is tested with injected, signed test sessions and uses HF OAuth in production.

## Deploy

`python tools/deploy_leaderboard.py` stages only the required source files, creates the Docker Space if absent, configures OAuth in its card, and uploads one commit. It requires an authenticated HF account with Space and private bucket access. `QAB_BUCKET` names the private bucket. `SESSION_SECRET` and the server's `HF_TOKEN` are Space secrets, never frontend variables.

## Persistence contract

Run exactly one worker and one Space replica. Its publishing lock serializes system-name claims and result replacements per CPU/GPU profile. Both profiles share one owner; replacing one does not affect the other. Each confirmed submission is a complete, immutable JSON object under `submissions/<random-id>.json`, including predictions, public metadata, private owner subject/username/email, timestamp, previous submission ID, corpus version/fingerprint and scorer version. The write is read back before publication succeeds. A crash after storage but before responding may leave a published record; the next preview detects that replacement.

The bucket is mutable storage, so application history uses new object names rather than overwriting old submissions. Records are retained indefinitely. Startup rebuilds the current system index from recursive listing; RAM and local caches are disposable. No database or mounted filesystem is required. A database becomes necessary before multiple writers or replicas are introduced. Never put SQLite on a bucket mount.

Public endpoints explicitly project public fields; they do not return full scorer reports, predictions, email, or account identity. Upload previews are temporary and are not persisted. Sign-in uses OIDC state/nonce/PKCE verification. Publication requires a signed, expiring preview digest, exact file/metadata agreement, latest corpus, authenticated ownership, an unchanged prior entry, and explicit confirmation.

## Corpus and scoring updates

`corpora.json` selects the corpus version/config and its Parquet path. All fields are loaded from the current dataset, with a 60-second server refresh cache and no commit pin. Keep prior entries for historical browsing. Scoring currently supports v1 with scorer 0.2.0. Alignment confidence is segment-native: green is at least 0.80, amber is 0.60 to below 0.80, and red is below 0.60; the board shows Trusted coverage and Unsafe green. Metrics content is versioned in source alongside the scorer. Alignment, waqf segmentation, and word timing have separate scorers and leaderboard views.

## Checks

```powershell
python -m pytest -q
python -m ruff check src tests leaderboard tools/deploy_leaderboard.py
cd leaderboard/frontend
npm run check
npm run build
```


Parallel tasks use the existing private store and OAuth flow. The combined preview shows every selected task. One publication writes one immutable batch object containing all selected task records, and startup expands it into independent leaderboard entries. The frontend keeps independent task/CPU/GPU drafts. Legacy single-task endpoints remain compatible. Replacement identity is `(system key, corpus, hardware class, task)`. Existing records without a task remain alignment results. Task scorer version and segment-aware corpus fingerprint are retained with new task artifacts. No migration or deletion of old submissions is needed.
