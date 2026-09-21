# quran-alignment-benchmark

Scorer, schemas and corpus tooling for the Quran recitation alignment benchmark. Package `qab`, CLI `qab`.

## Read first

- `README.md`: consumer contract (task, metrics, submission format, how to score locally).
- `docs/SPEC.md`: scorer arithmetic; the code implements it section by section. Change both or neither.
- `docs/CORPUS.md`: dataset columns, id rule, how the truth was produced, known gaps.
- `tools/dataset_card.md`: the Hub card. Brief; links to the README instead of repeating it.

## Layout

- `src/qab/hafs.py` bundled Hafs table (QUL QPC Hafs text); `refs.py` reference grammar and matching keys; `schema.py` pydantic contracts; `scoring.py` per-case scoring; `report.py` pooling, slices, report document; `corpus.py` Hub/local loading and `fetch`; `cli.py`.
- `tests/test_scoring.py`: one test per SPEC section 7 rule. A scoring change without a test is not done.
- `tools/export_corpus.py` + `tools/corpus_<version>.json`: maintainer exporter from the Inspector bucket to parquet (needs bucket access and ffprobe). `build/` is scratch, ignored.

## Conventions

- Pure Python, pydantic only; `datasets`/`huggingface_hub` behind the `corpus` extra. No network in scoring.
- Constants of the benchmark (`TAU_PAD_S`, `EPSILON_S`, `MAX_OVERLAP_S`) are versioned with the scorer, never parameters.
- A corpus config (`v1`) is immutable once scored against; changes go to the next config. Reports carry `scorer_version` (package version = git tag) and `corpus_fingerprint`.
- Install by git tag, not PyPI: `pip install "qab[corpus] @ git+https://github.com/Hetchy/quran-alignment-benchmark@v0.2.0"`.
- License: CC BY 4.0 for code and annotations, not the audio.
- `ruff check src tests` and `pytest -q` must pass (CI). Commit messages: `type(scope): what` (feat, fix, docs, test, chore).

## Private submission API

Keep this API out of the README, public documentation, frontend, and generated API documentation. Its routes use
`include_in_schema=False`, and FastAPI's OpenAPI, Swagger, and ReDoc routes stay disabled.

- Authenticate every call with `Authorization: Bearer <hf-token>`. The server resolves the token with
  `HfApi().whoami()`, uses the returned Hugging Face user ID as the private owner key, and never stores the token.
- Send `x-qab-request: 1` on POST requests.
- `GET /api/v1/submissions/me` checks the token and returns the private Hugging Face username.
- `POST /api/v1/submissions/preview` accepts multipart `files` plus a JSON string in the `metadata` form field. It
  returns per-task validation/scores and a one-hour `preview_token` when the upload is complete.
- `POST /api/v1/submissions/publish` accepts the same `files` and `metadata`, plus the exact `preview_token` and
  `confirmed=true`. Publication is rejected if files, metadata, selected tasks, or replacement state changed.
- `metadata.tasks` may contain any non-empty subset of `alignment`, `segmentation`, and `timing`. A single-task
  upload may be flat. Multi-task uploads use `alignment/`, `segmentation/`, and `timing/` folders for the selected
  tasks. All selected tasks publish atomically.
- Metadata fields are `system`, `description`, `url`, private `email`, `user_parameters`, `hardware_class` (`cpu`,
  `gpu`, or null), `hardware`, and `tasks`. CPU/GPU profiles and task replacements are independent under one system
  owner.
