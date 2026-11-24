# Umbrella Enterprise AI: Setup Notes

A running summary of what has been set up, what each file does, and what to do next.
No secrets are stored here. Real values live only in `.env` (gitignored).

## 1. What the app is

A document Q&A (RAG) app. Three services each hold one kind of data:

| Service | Holds | Status |
|---|---|---|
| **S3** (`S3_BUCKET`) | Uploaded files | Working (bucket empty) |
| **Pinecone** (`PINECONE_INDEX_NAME`) | Vector embeddings for search (1024 dims, cosine) | Working, index ready |
| **Postgres on Render** (`DATABASE_URL`) | Metadata, access control, audit log, chat history | Working, 5 tables created |
| **AWS Bedrock** (Titan embeddings, Nova 2 Lite chat) | Embeddings and chat answers | **Blocked**, see section 6 |

## 2. Build order (what comes first, what comes second)

Files are created as needed, not all at once. Order so far, and the suggested order ahead:

| # | File | Status | Why this position |
|---|---|---|---|
| 1 | `pyproject.toml` / `uv.lock` | Done | Dependencies installed with `uv add` |
| 2 | `.env` and `.env.example` | Done | Settings come before any code can use them |
| 3 | `app/config.py` | Done | Reads `.env`; every other file imports it |
| 4 | `infra/schema.sql` | Done | Blueprint of the database tables |
| 5 | `scripts/init_db.py` | Done, run once | Builds the tables from the blueprint |
| 6 | `app/db.py` | Done | Lets the running app use the tables. Needs steps 3 to 5 first |
| 7 | `app/aws.py` | Next | S3 and Bedrock clients |
| 8 | `app/vector.py` | Next | Pinecone read and write (testable now) |
| 9 | `app/main.py` | Later | FastAPI app and a health check |
| 10 | `app/rag.py`, `memory.py`, `guardrails.py`, `graph.py` | Later | Retrieval and chat logic; needs Bedrock working |
| 11 | `worker/ingest.py`, `app/templates/`, `app/static/` | Later | Document processing and the UI |

## 3. What each file does

**`infra/schema.sql`: the blueprint.** Defines 5 tables. Every statement uses `IF NOT EXISTS`, so it is safe to rerun.

| Table | Purpose |
|---|---|
| `documents` | One row per uploaded file: title, owner, allowed groups, S3 key, status (`pending`, `processing`, `ready`, `failed`, `retired`) |
| `feedback` | Thumbs up/down (-1, 0, 1) with a note |
| `audit_events` | Who did what, with JSON detail |
| `approvals` | Questions waiting for a human reviewer |
| `chat_messages` | Saved conversation history |

`tenant_id` on every table lets several organisations share one database without seeing each other's data.

**`scripts/init_db.py`: the builder (run by hand).** Reads `schema.sql`, runs it in one transaction, and checks all 5 tables exist. Run it once per new or empty database:

```
uv run scripts/init_db.py
```

The app never calls it. It is already done for the current database.

**`app/db.py`: the door the app uses every day.** Imported by FastAPI while it runs.

- `connection()`: borrows a pooled connection; commits on success, rolls back on error; rows come back as dicts.
- `audit(conn, actor, tenant, action, detail)`: writes a row to `audit_events`.
- `close_pool()`: call at app shutdown.
- Raises a clear error if `DATABASE_URL` is missing.
- Assumes the tables already exist.

**`app/config.py`: settings.** `settings()` returns one cached object holding every `.env` value, with types and defaults. Use it as `from app.config import settings; settings().s3_bucket`. Includes `max_upload_bytes` (default 10 MB). The app must still check upload size itself.

**schema.sql vs init_db.py vs db.py in one line:** the blueprint, the builder that runs it once, and the door the app uses afterwards.

## 4. Environment variables (`.env`)

Names only. See `.env.example` for the template.

- AWS: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET`
- Uploads: `MAX_UPLOAD_BYTES` (default 10485760)
- Bedrock: `BEDROCK_EMBED_MODEL`, `BEDROCK_CHAT_MODEL`, `EMBED_DIM` (1024)
- Pinecone: `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_CLOUD`, `PINECONE_REGION`, `PINECONE_READY_TIMEOUT`
- Postgres: `DATABASE_URL`
- Observability: `RIUS_API_KEY`, `RIUS_SERVICE_NAME`, `RIUS_DISABLED`, `RIUS_CAPTURE_CONTENT`

Fixes made along the way:
- `AWS-REGION` renamed to `AWS_REGION` (a hyphen is not valid in an environment variable name).
- `PINECONE_INDEX_HOST` renamed to `PINECONE_INDEX_NAME` (the value is an index name, not a host URL).

## 5. Tests

Run from the project root with `uv run`. No `pytest` needed.

| Command | Checks |
|---|---|
| `uv run test/test_config.py` | `app/config.py`: defaults, overrides, validation, `.env` loading (7 tests) |
| `uv run test/test_services.py` | S3, Postgres, Pinecone connectivity |
| `uv run test/test.py` | Bedrock Titan embeddings and Nova 2 Lite chat |

## 6. Known issue: Bedrock is blocked

- Titan and Nova calls fail with `ValidationException: Operation not allowed`, in code and in the console playground. Credentials, region and model IDs are fine.
- AWS account verification is complete. The account is on the **Free plan**.
- Other services work (S3, Lambda, DynamoDB, EC2, SQS, SNS, IAM, and more). Textract, Comprehend and Translate ask for a subscription.
- The cause is not confirmed. Likely: a Free plan restriction or a new-account delay.

Next steps, in order:
1. Wait about 24 hours from the verification time (Nov 23, 3:15 PM UTC-5), then rerun `uv run test/test.py`.
2. If still blocked, upgrade to the Paid plan in AWS Settings, then Billing.
3. If still blocked, open an AWS Support case quoting the error.

The Anthropic "use case details" form in Bedrock is not needed for Titan or Nova. It only applies to Claude models.

## 7. Tooling set up

- **uv** manages dependencies. Add packages with `uv add <package>`; run code with `uv run`.
- **AWS CLI 2.42.2** is installed under `~/.local`. Profile `umbrella` uses region `us-east-1` and signs in with `aws login`. The session is renewable.
- **Agent Toolkit for AWS** is installed: skills in `~/.claude/skills` and the `aws-mcp` server in `~/.claude.json`. Start a new session to load it.
- **`CLAUDE.md`** holds the toolkit's "new AWS experience" rules. They say to create Regional resources only in the project's Region and to ask for a help level (low, medium or high) when building on AWS.

## 8. SQLTools tips (VS Code)

- You do not need pgAdmin. SQLTools connects to the same database.
- To see tables: SQLTools sidebar, then `umbrella`, `umbrella_db_7lwo`, `public`, `Tables`. Use the refresh icon if they are missing.
- `umbrella.session.sql` is a scratch file for your own queries. Empty is normal. Run a selection with Cmd+E Cmd+E.
- Password prompt: SQLTools only asks if the connection has `"askForPassword": true`. Without it, it tries an empty password and shows "Error opening connection". The password is the part of `DATABASE_URL` between the `:` after the username and the `@`. It is not the Pinecone key.
- Keep `.vscode/` and `umbrella.session.sql` out of git.

## 9. Git state

Pushed: `a07bab2` (dependencies, `.env.example`).
Committed locally: `e536ae3` (settings, tests, `CLAUDE.md`).
Not committed yet: the `app/config.py` move, `app/`, `infra/`, `scripts/`, `test/test_config.py`, `docs/`, and the `.env.example` update.

## 10. Habits worth keeping

- Never put real secrets in `.env.example`, code, or docs. `.env` is gitignored.
- Always pass SQL values as parameters (`%s`), never build SQL with f-strings.
- Create files as you need them. His full skeleton is a map, not a checklist.
- Dependencies live in `pyproject.toml`. Skip `requirements.txt`.
