# Umbrella Enterprise AI: Setup Notes

A running summary of what has been set up, what each file does, and what to do next.
No secrets are stored here. Real values live only in `.env` (gitignored).
Last updated: Nov 25, 2026.

## 1. What the app is

A document Q&A (RAG) app, built by following the public reference project
https://github.com/entbappy/ATLAS-Enterprise-AI (cloned only for reading; never run).
Several services each hold one kind of data or do one job:

| Service | Holds / does | Status |
|---|---|---|
| **S3** (`S3_BUCKET`) | Uploaded files | Working, tested (browser upload works; bucket allows `PUT` from any origin) |
| **Pinecone** (`PINECONE_INDEX_NAME` = `umbrella-vectors`) | Vector embeddings for search (1024 dims, cosine) | Working, tested |
| **Postgres on Render** (`DATABASE_URL`) | Metadata, access control, audit log, chat history | Working, 5 tables created |
| **LangSmith** (or Rius) | Tracing: step timing, token counts | Working, tested (see section 4) |
| **OpenAI** (`text-embedding-3-large`) | Turns text into vectors | Working, tested |
| **Anthropic** (`claude-haiku-5-5`) | Writes the final answers | Working, tested |
| **AWS Bedrock** (Titan, Nova) | The reference app's original embeddings and chat | **Blocked on this account**, replaced by OpenAI and Anthropic (see section 7) |

## 2. How the pieces fit together

When someone asks a question (`POST /api/chat` in `app/main.py`):
1. `main.py` checks the input guardrail and, if the question looks sensitive ("delete", "export", ...), holds it in the `approvals` table instead.
2. Otherwise it loads the recent conversation (`memory.history`) and runs `graph.chat`, which does:
   - **guard**: rejects empty or attack-style questions.
   - **retrieve**: `embed(question)` (OpenAI), then `search()` in Pinecone as this user; hits scoring below `MIN_RELEVANCE` are dropped.
   - **generate**: `answer()` (Anthropic) writes the reply from the passages and cites them as `[1]`, `[2]`. With no passages the graph skips the model and returns a fixed "no evidence" reply.
   - **validate**: replaces any answer that leaks a secret.
3. `main.py` saves the turn (`memory.save_turn`) and returns the answer, sources and conversation id.

When someone uploads a document: the browser asks `POST /api/documents/uploads` for a temporary S3 link and a `pending` row, sends the file straight to S3, then calls `POST /api/documents/complete`. That runs `worker/ingest.py`: read the file from S3, split it into overlapping chunks, `embed()` each chunk, `upsert()` them into Pinecone, and mark the row `ready` in Postgres.

## 3. Build order (what comes first, what comes second)

Files are created as needed, not all at once. The reference project's own order is:
setup, then ingestion and RAG, then observability, then FastAPI and UI, then integration.

| # | File | Status | Why this position |
|---|---|---|---|
| 1 | `pyproject.toml` / `uv.lock` | Done | Dependencies installed with `uv add` |
| 2 | `.env` and `.env.example` | Done | Settings come before any code can use them |
| 3 | `app/config.py` | Done | Reads `.env`; every other file imports it |
| 4 | `infra/schema.sql` | Done | Blueprint of the database tables |
| 5 | `scripts/init_db.py` | Done, run once | Builds the tables from the blueprint |
| 6 | `app/db.py` | Done | Lets the running app use the tables. Needs steps 3 to 5 first |
| 7 | `app/observability.py` | Done | Tiny tracing helper; `vector.py` imports it |
| 8 | `app/vector.py` | Done | Pinecone read/write. Needs config and observability |
| 9 | `app/aws.py` | Done (adapted) | `embed()` and `answer()`. Needs config and observability |
| 10 | `app/guardrails.py`, `app/demo_context.py`, `app/memory.py` | Done | Small; no AWS needed. `memory.py` feeds `graph.py` its `history` |
| 11 | `app/graph.py` | Done | Wires guard, retrieve, generate, validate. Needs `aws.py`, `vector.py`, `guardrails.py`. (`rag.py` from the reference was dropped: it only re-exported `chat`.) |
| 12 | `worker/ingest.py` | Done | Document processing. Needs db, aws, vector |
| 13 | `app/main.py`, `app/templates/`, `app/static/` | Done | The FastAPI app and UI. Needs everything above |
| 14 | `README.md`, `docs/SETUP.md` | Done | Project overview and these notes |
| 15 | `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`, `render.yaml` | Done (not yet run on GitHub or Render) | Packaging, CI and deployment. The reference deploys to AWS EC2 with a self-hosted runner; this project uses GitHub Actions for CI and Render for deployment |

## 4. What each file does

**`infra/schema.sql`: the blueprint.** Defines 5 tables. Every statement uses `IF NOT EXISTS`, so it is safe to rerun.

| Table | Purpose |
|---|---|
| `documents` | One row per uploaded file: title, owner, allowed groups, S3 key, status (`pending`, `processing`, `ready`, `failed`, `retired`) |
| `feedback` | Thumbs up/down (-1, 0, 1) with a note |
| `audit_events` | Who did what, with JSON detail |
| `approvals` | Questions waiting for a human reviewer |
| `chat_messages` | Saved conversation history |

`tenant_id` on every table lets several organisations share one database without seeing each other's data.

**`scripts/init_db.py`: the builder (run by hand).** Reads `schema.sql`, runs it in one transaction, and checks all 5 tables exist. Run it once per new or empty database: `uv run scripts/init_db.py`. The app never calls it. Already done for the current database.

**`app/db.py`: the door the app uses every day.**
- `connection()`: borrows a pooled connection; commits on success, rolls back on error; rows come back as dicts.
- `audit(conn, actor, tenant, action, detail)`: writes a row to `audit_events`.
- `close_pool()`: call at app shutdown.
- Raises a clear error if `DATABASE_URL` is missing. Assumes the tables already exist.

**`app/config.py`: settings.** `settings()` returns one cached object holding every `.env` value, with types and defaults: `from app.config import settings; settings().s3_bucket`. Includes `max_upload_bytes` (default 10 MB; the upload code must still enforce it). `get_settings` is an alias.

**`app/vector.py`: the librarian (Pinecone).**
- `index()`: connects to the index, checks size (1024) and metric (cosine), waits until ready.
- `upsert(tenant, records)`: stores chunk vectors in that tenant's own namespace (`tenant-<name>`).
- `search(tenant, vector, groups, owner)`: returns the closest chunks the user may see (member of an allowed group, or the owner). Other tenants' data is never searched.
- `delete_document(tenant, ids)`: removes chunks.
- Copied unchanged from the reference project.

**`app/observability.py`: the fitness tracker, switchable.** `@trace("name", kind=...)` labels a function so a tracing tool can show its timing and steps. Two settings in `.env` choose how it behaves:
- `OBSERVABILITY_PROVIDER`: `langsmith` (default), `rius` or `none`. Switching is a `.env` edit plus a restart; no code changes.
- `OBSERVABILITY_CAPTURE`: `off` (timing and errors only), `metadata` (default: also token counts and the model name, no text) or `full` (also prompts, evidence and answers; use only with fake data).

`record_usage(model, vendor, input_tokens, output_tokens)` adds the token numbers to the current trace; `aws.py` calls it after each OpenAI and Anthropic call. A tool only sends data if its key is set (`LANGSMITH_API_KEY` or `RIUS_API_KEY`). The LangSmith project (`LANGSMITH_PROJECT`, default `umbrella`) is created automatically on the first trace. `initialize()` must run before any traced call; `main.py` and `worker/ingest.py` call it at startup. Tracing errors are swallowed, so tracing can never break the app.

**`app/aws.py`: the translator and the writer.**
- `embed(text)`: OpenAI `text-embedding-3-large`, asked for 1024 numbers so it matches the Pinecone index.
- `answer(question, evidence, history)`: Anthropic `claude-haiku-5-5`. Cites evidence as `[1]`, says it cannot find an answer when evidence is weak, and uses the recent conversation.
- `client("s3")`: the S3 client, using the AWS keys from `.env`.
- Function names match the reference project, so other files work unchanged. The only change: no `temperature` setting, because the installed Anthropic SDK (2.14.0) does not accept it.

**`app/guardrails.py`: the bouncer.** `classify_input` / `check_input` reject empty questions and attack patterns ("ignore previous instructions", "reveal your system prompt", "send all api keys"); an ordinary question about a password policy passes. `check_output` replaces an answer that contains a private key, an AWS key or `password: ...`. This is simple pattern matching, not a substitute for the access filter in the search. Copied unchanged from the reference.

**`app/graph.py`: the pipeline.** A small LangGraph: `guard`, then `retrieve`, then (`generate`, `validate`) or `no_evidence`. `chat(principal, question, history)` is the one function the web app calls. A follow-up such as "and per month?" is searched together with the previous user question.

**`app/memory.py`: conversation memory.** `history()` returns the last `MEMORY_TURNS` question/answer pairs (oldest first), `save_turn()` stores one pair, `list_messages()` returns the whole thread. Every query filters on tenant and conversation id.

**`app/auth.py`: the shared-password gate.** If `APP_PASSWORD` is set, a middleware in `main.py` answers 401 (so the browser shows its login box) unless the request carries HTTP Basic credentials with that password; any username works. The comparison is constant-time. Only `/health/live` is exempt. Empty `APP_PASSWORD` means no password (local development); the app logs a warning at startup in that case. One shared password, not user accounts; use a long random value and only over HTTPS.

**`app/demo_context.py`: who is asking.** One fixed `Principal` (subject `demo-operator`, tenant `demo-workspace`, group `workspace`, admin). Every endpoint receives it via `Depends(principal)`. Real login later means changing only `principal()`.

**Citations:** after the answer is written, `graph.validate` keeps only the sources the answer actually cites (`[1]`, `[1, 3]`), so the interface no longer lists passages the model did not use. If it can match no citation, every source is kept.

**`worker/ingest.py`: the librarian's assistant.** `process_s3_key(key)` locks the document row, downloads the file (size and PDF header checks), parses it (PDF by page, DOCX, text), cuts it into 1200-character chunks that overlap by 180, embeds and upserts them in batches of 50, then marks the row `ready` (or `failed`) and writes an audit event. Scanned PDFs are not supported (no OCR).

**`app/main.py`: the web API.** Upload (two steps), document list, chat, saved conversations, feedback, admin metrics, approvals (list and decide) and guardrail counts. Approving a request only records the decision; nothing is executed. Everything is filtered by the user's tenant.

**`app/templates/index.html`, `app/static/`: the interface.** Four tabs: Knowledge chat, Documents, Approvals, Metrics. The Documents tab uploads up to 10 files at a time, one after another, with a status line per file; a failing file (wrong type, over 10 MB, server error) is reported and the rest continue. Answers are rendered from simple markdown (lists, bold) by building DOM elements, never `innerHTML`. Enter sends, a refresh restores the conversation, and the welcome block hides once you start chatting.

**`sample_docs/`:** seven fake documents for trying the app, in four file types (text, Markdown, Word, PDF): leave, onboarding, IT security, holidays, expenses, remote work, travel. The guide `docs/SAMPLE_DOCS.md` lists a question and the expected answer for each (it is kept outside the folder so uploading the folder does not index the guide).

**schema.sql vs init_db.py vs db.py in one line:** the blueprint, the builder that runs it once, and the door the app uses afterwards.

## 5. Environment variables (`.env`)

Names only. See `.env.example` for the template.

- AWS: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET`
- Uploads: `MAX_UPLOAD_BYTES` (default 10485760)
- OpenAI / Anthropic: `OPENAI_API_KEY`, `OPENAI_EMBED_MODEL` (`text-embedding-3-large`), `ANTHROPIC_API_KEY`, `ANTHROPIC_CHAT_MODEL` (`claude-haiku-5-5`)
- Embeddings size: `EMBED_DIM` (1024, must match the Pinecone index)
- Pinecone: `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_CLOUD`, `PINECONE_REGION`, `PINECONE_READY_TIMEOUT`
- Postgres: `DATABASE_URL`
- Access: `APP_PASSWORD` (shared password for the whole app; empty = open)
- RAG behaviour: `MIN_RELEVANCE` (0.30), `MEMORY_TURNS` (6), `HITL_ENABLED` (True)
- Observability: `OBSERVABILITY_PROVIDER` (`langsmith` | `rius` | `none`), `OBSERVABILITY_CAPTURE` (`off` | `metadata` | `full`), `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `RIUS_API_KEY`, `RIUS_SERVICE_NAME`, `RIUS_DISABLED`. (The capture setting controls Rius too.)

Fixes made along the way:
- `AWS-REGION` renamed to `AWS_REGION` (a hyphen is not valid in an environment variable name).
- `PINECONE_INDEX_HOST` renamed to `PINECONE_INDEX_NAME` (the value is an index name, not a host URL).
- Nova chat model changed to the inference profile ID `us.amazon.nova-2-lite-v1:0` (the plain ID cannot be called on demand).

## 6. Pinecone: why the index changed

The first index, `umbrella-ai-index`, was created as a newer kind of index with a "document schema". It rejects the classic `upsert(vectors=...)` write the reference code uses (HTTP 400: writes must go through the documents API). The code was fine; the index type was the problem.

Fix: a new classic index, `umbrella-vectors` (1024 dims, cosine, serverless AWS `us-east-1`). The old index is empty and unused; delete it in the Pinecone console to free a slot (the free plan allows 5 indexes). Embeddings from different models must not share one index, so if the embedding model ever changes, re-embed everything into a fresh index.

## 7. Known issue: Bedrock is blocked (replaced by OpenAI + Anthropic)

- Titan and Nova calls fail with `ValidationException: Operation not allowed`, in code and in the console playground.
- The reason: the AWS availability check says `authorizationStatus: NOT_AUTHORIZED` for both models, while entitlement and region are `AVAILABLE`. There is no agreement to accept ("Agreement not supported for this model"), so only AWS can authorize the account.
- Ruled out: credentials (valid, same account), model IDs (fixed), account verification (completed Nov 23), a 24-hour wait (retested Nov 24, still blocked). AWS's own docs list Bedrock as included in the Free plan, so upgrading to Paid is not expected to fix it.
- Next step if Bedrock is wanted: open an AWS Support case (Account and billing type is free) quoting the error and the `NOT_AUTHORIZED` result.
- Claude on Bedrock would also need Anthropic's one-time use-case form; Titan and Nova do not.
- Other AWS services work (S3, Lambda, DynamoDB, EC2, SQS, SNS, IAM, Secrets Manager, Polly, Rekognition). Textract, Transcribe and Translate need the Paid plan. So scanned PDFs cannot be OCR'd; normal PDF, DOCX, TXT and MD files work.

## 7b. Tuning `MIN_RELEVANCE` (measured Nov 25, 2026)

Best-match scores on the three sample documents (cosine, 0 to 1):

| Question type | Best document | Wrong documents |
|---|---|---|
| Related (7 questions) | 0.57, 0.43, 0.65, 0.55, 0.47, 0.33, 0.24 (a follow-up asked alone; the app adds the previous question, which raises it) | 0.11 to 0.37 |
| Unrelated (5 questions) | 0.23, 0.09, 0.08, 0.14, 0.11 | n/a |

Set to 0.30: above every unrelated score, below every real match. The numbers cannot fully remove wrong-document sources, because some wrong documents score up to 0.37 (higher than the weakest right answer, 0.33). Raising the value further would start rejecting real questions. If the extra sources still bother you, the cleaner fix is to show only the sources the answer actually cites, or to keep only hits close to the top score. With a larger and more varied document set the numbers will differ; re-measure.

## 8. Tests

Run from the project root with `uv run`. No `pytest` needed.

| Command | Checks |
|---|---|
| `uv run test/test_config.py` | `app/config.py`: defaults, overrides, validation, `.env` loading (7 tests, no network) |
| `uv run test/test_services.py` | S3, Postgres, Pinecone connectivity |
| `uv run test/test_vector.py` | `app/vector.py` against the real index: store, search, access rules, tenant isolation, delete. Uses a throwaway namespace and cleans up |
| `uv run test/test_aws.py` | `app/aws.py`: OpenAI embeddings (size and meaning), Anthropic answers, S3 client. Makes real calls costing a fraction of a cent |
| `uv run test/test_observability.py` | The tracing switch: all providers and capture levels leave behaviour unchanged; capture levels send or hide text (fake client); a live LangSmith run arrives with token counts and no text; a live Rius span |
| `uv run test/test_ingest.py` | `worker/ingest.py` end to end: real S3, Postgres, OpenAI, Pinecone. Options: `--keep` leaves the file, rows and vectors so you can look at them; `--cleanup` removes them |
| `uv run test/test_graph.py` | Guardrails and routing (no network), then a real run of `chat()`: allowed user gets a cited answer, outsider and unrelated question get "no evidence", injection is blocked |
| `uv run test/test_memory.py` | `app/memory.py` against Postgres: order, the `MEMORY_TURNS` cap, conversation and tenant separation, special characters |
| `uv run test/test_auth.py` | The password gate with no keys: right/wrong/near-miss passwords, malformed headers, every kind of URL protected, `/health/live` open, and open when no password is set (24 checks) |
| `uv run test/test_main.py` | The whole web API through FastAPI's test client: upload to S3 and ingest, chat, memory, approvals, admin-only access, feedback, metrics, tenant isolation (32 checks) |

## 9. Tooling set up

- **Run the app:** `uv run uvicorn app.main:app --reload`, then open http://localhost:8000. Upload the files in `sample_docs/` from the Documents tab, then ask questions. Static files (CSS, JS) are served from disk, so a browser refresh picks up changes; Python changes reload automatically with `--reload`.

- **uv** manages dependencies. Add packages with `uv add <package>`; run code with `uv run`.
- **AWS CLI 2.42.2** is installed under `~/.local`. Profile `umbrella` uses region `us-east-1` and signs in with `aws login`.
- **Agent Toolkit for AWS** is installed: skills in `~/.claude/skills` and the `aws-mcp` server in `~/.claude.json`. Start a new session to load it.
- **`CLAUDE.md`** holds the toolkit's "new AWS experience" rules (Regional resources only in the project's Region; ask for a help level when building on AWS).

## 10. SQLTools tips (VS Code)

- You do not need pgAdmin. SQLTools connects to the same database.
- To see tables: SQLTools sidebar, then `umbrella`, `umbrella_db_7lwo`, `public`, `Tables`. Use the refresh icon if they are missing.
- `umbrella.session.sql` is a scratch file for your own queries. Empty is normal. Run a selection with Cmd+E Cmd+E.
- Password prompt: SQLTools only asks if the connection has `"askForPassword": true`. Without it, it tries an empty password and shows "Error opening connection". The password is the part of `DATABASE_URL` between the `:` after the username and the `@`. It is not the Pinecone key.
- Keep `.vscode/` and `umbrella.session.sql` out of git.

## 11. Git state

Everything up to the interface is committed and pushed to GitHub (`main`). Changes made after that and not yet committed: the `MIN_RELEVANCE` change, this file, `README.md`, and the deletion of the leftover `main.py` that `uv init` created in the project root (the real app file is `app/main.py`).
Leave out of commits: `.vscode/` and `umbrella.session.sql` (local editor files). `.env` is gitignored.

## 12. CI/CD and deployment

- **`Dockerfile`**: two stages. Stage 1 installs the exact dependencies from `uv.lock`; stage 2 copies only that environment plus `app/`, `worker/`, `scripts/` and `infra/`. Runs as a non-root user (uid 10001), has a health check on `/health/live`, and listens on `$PORT` (default 8000). No secrets are baked in; settings come from environment variables.
- **`.dockerignore`**: keeps `.env`, `.git`, `.venv`, tests, docs and sample documents out of the image. The CI job also checks the image has no `.env`.
- **`.github/workflows/ci.yml`**: job `test` (locked install, compile everything, `test_config`, `test_graph --offline`, `test_observability`) then job `docker` (build, start, check `/health/live`, `/`, the JS and the CSS). It uses no secrets, so the tests that call real services stay manual.
- **`render.yaml`**: a Render blueprint for one Docker web service in the Oregon region (same as the database). `autoDeployTrigger: checksPass` means Render deploys only after CI passes. Secret variables are `sync: false`: Render asks for them once when the service is created.
- Tested locally: the three offline test steps in a clean copy without `.env`, and the image build, start and health check. Not yet run: the workflow on GitHub and the deploy on Render.
- **Access control:** a deployed service is public. Set `APP_PASSWORD` (a secret in `render.yaml`, entered in the Render dashboard); the browser then asks for it before showing anything. Only `/health/live` stays open, for Render's health check. Without it, anyone with the URL can use your API credits and see your documents.
- Tests that need real services are run by hand: `test_vector`, `test_aws`, `test_ingest`, `test_memory`, `test_main`, and the live part of `test_graph` and `test_observability`.

## 13. Habits worth keeping

- Never put real secrets in `.env.example`, code, or docs. `.env` is gitignored.
- Always pass SQL values as parameters (`%s`), never build SQL with f-strings.
- Create files as you need them. The reference project's layout is a map, not a checklist.
- Dependencies live in `pyproject.toml`. Skip `requirements.txt`.
- Read third-party code before using it; run only what you understand.
