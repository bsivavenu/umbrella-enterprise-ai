# Umbrella Enterprise AI: Setup Notes

A running summary of what has been set up, what each file does, and what to do next.
No secrets are stored here. Real values live only in `.env` (gitignored).
Last updated: Nov 24, 2026.

## 1. What the app is

A document Q&A (RAG) app, built by following the public reference project
https://github.com/entbappy/ATLAS-Enterprise-AI (cloned only for reading; never run).
Several services each hold one kind of data or do one job:

| Service | Holds / does | Status |
|---|---|---|
| **S3** (`S3_BUCKET`) | Uploaded files | Working (bucket empty) |
| **Pinecone** (`PINECONE_INDEX_NAME` = `umbrella-vectors`) | Vector embeddings for search (1024 dims, cosine) | Working, tested |
| **Postgres on Render** (`DATABASE_URL`) | Metadata, access control, audit log, chat history | Working, 5 tables created |
| **OpenAI** (`text-embedding-3-large`) | Turns text into vectors | Working, tested |
| **Anthropic** (`claude-haiku-5-5`) | Writes the final answers | Working, tested |
| **AWS Bedrock** (Titan, Nova) | The reference app's original embeddings and chat | **Blocked on this account**, replaced by OpenAI and Anthropic (see section 7) |

## 2. How the pieces fit together

When someone asks a question:
1. `embed(question)` in `app/aws.py` turns the question into a vector (OpenAI).
2. `search()` in `app/vector.py` finds the closest chunks in Pinecone, returning only chunks the user may see.
3. `answer()` in `app/aws.py` writes the reply from those chunks (Anthropic).
4. `app/memory.py` and `app/db.py` save the conversation in Postgres; `audit()` records events.

When someone uploads a document (later, `worker/ingest.py`): read the file from S3, split it into chunks, `embed()` each chunk, `upsert()` them into Pinecone, and mark the row `ready` in Postgres.

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
| 10 | `app/guardrails.py`, `demo_context.py`, `memory.py` | Next | Small; no AWS needed |
| 11 | `app/graph.py`, `app/rag.py` | Later | Wires guard, retrieve, generate, validate. Needs `aws.py` and `vector.py` |
| 12 | `worker/ingest.py` | Later | Document processing. Needs db, aws, vector |
| 13 | `app/main.py`, `app/templates/`, `app/static/` | Later | The FastAPI app and UI. Needs everything above |

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

**`app/observability.py`: the fitness tracker.** `@trace("name")` labels a function so Rius can show its timing and steps. With no `RIUS_API_KEY`, or with `RIUS_DISABLED=true`, it does nothing. It never sends function inputs or outputs. Only the default service name was changed (to `umbrella_ai`).

**`app/aws.py`: the translator and the writer.**
- `embed(text)`: OpenAI `text-embedding-3-large`, asked for 1024 numbers so it matches the Pinecone index.
- `answer(question, evidence, history)`: Anthropic `claude-haiku-5-5`. Cites evidence as `[1]`, says it cannot find an answer when evidence is weak, and uses the recent conversation.
- `client("s3")`: the S3 client, using the AWS keys from `.env`.
- Function names match the reference project, so other files work unchanged. The only change: no `temperature` setting, because the installed Anthropic SDK (2.14.0) does not accept it.

**schema.sql vs init_db.py vs db.py in one line:** the blueprint, the builder that runs it once, and the door the app uses afterwards.

## 5. Environment variables (`.env`)

Names only. See `.env.example` for the template.

- AWS: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET`
- Uploads: `MAX_UPLOAD_BYTES` (default 10485760)
- OpenAI / Anthropic: `OPENAI_API_KEY`, `OPENAI_EMBED_MODEL` (`text-embedding-3-large`), `ANTHROPIC_API_KEY`, `ANTHROPIC_CHAT_MODEL` (`claude-haiku-5-5`)
- Embeddings size: `EMBED_DIM` (1024, must match the Pinecone index)
- Bedrock (not used right now): `BEDROCK_EMBED_MODEL`, `BEDROCK_CHAT_MODEL` (`us.amazon.nova-2-lite-v1:0`)
- Pinecone: `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_CLOUD`, `PINECONE_REGION`, `PINECONE_READY_TIMEOUT`
- Postgres: `DATABASE_URL`
- Observability: `RIUS_API_KEY`, `RIUS_SERVICE_NAME`, `RIUS_DISABLED`, `RIUS_CAPTURE_CONTENT`

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

## 8. Tests

Run from the project root with `uv run`. No `pytest` needed.

| Command | Checks |
|---|---|
| `uv run test/test_config.py` | `app/config.py`: defaults, overrides, validation, `.env` loading (7 tests, no network) |
| `uv run test/test_services.py` | S3, Postgres, Pinecone connectivity |
| `uv run test/test_vector.py` | `app/vector.py` against the real index: store, search, access rules, tenant isolation, delete. Uses a throwaway namespace and cleans up |
| `uv run test/test_aws.py` | `app/aws.py`: OpenAI embeddings (size and meaning), Anthropic answers, S3 client. Makes real calls costing a fraction of a cent |
| `uv run test/test.py` | Bedrock Titan and Nova (currently fails, see section 7) |
| `uv run test/test_claude.py` | Claude through Bedrock (currently fails, same reason) |

## 9. Tooling set up

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

Pushed to GitHub: `a07bab2`.
Committed locally, not pushed: `e536ae3`, `a35cfd5`.
Not committed yet: `app/vector.py`, `app/observability.py`, `app/aws.py`, the `app/config.py` and `.env.example` updates, the new tests, and the `openai` / `anthropic` dependencies.
Leave out of commits: `.vscode/`, `umbrella.session.sql`, and `test/test_claude.py` (Bedrock only).

## 12. Habits worth keeping

- Never put real secrets in `.env.example`, code, or docs. `.env` is gitignored.
- Always pass SQL values as parameters (`%s`), never build SQL with f-strings.
- Create files as you need them. The reference project's layout is a map, not a checklist.
- Dependencies live in `pyproject.toml`. Skip `requirements.txt`.
- Read third-party code before using it; run only what you understand.
