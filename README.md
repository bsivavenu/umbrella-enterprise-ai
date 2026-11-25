# Umbrella Enterprise AI

[![CI](https://github.com/bsivavenu/umbrella-enterprise-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/bsivavenu/umbrella-enterprise-ai/actions/workflows/ci.yml)

A document question-answering (RAG) web app. Upload company documents, then ask questions in plain
language and get answers that cite the exact passages they came from. Each user only ever gets
answers drawn from documents they are allowed to see.

Built step by step by following the public reference project
[ATLAS-Enterprise-AI](https://github.com/entbappy/ATLAS-Enterprise-AI), with the AWS Bedrock models
swapped for OpenAI and Anthropic (Bedrock was not available on the author's AWS account).

> **Status:** working demo. It uses a single fixed demo user (no login) and is not hardened for
> production. See [Limitations](#limitations).

## Screenshot

![Umbrella AI knowledge chat: a cited answer with its source passage](docs/Umbrella%20AI%20Interface%20Screenshot.png)

*Knowledge chat. The answer cites its sources as [1], and each source expands to show the passage it came from.*

## What it does

- **Knowledge chat** with cited sources, conversation memory, and follow-up questions.
- **Document upload** (PDF, DOCX, TXT, MD, up to 10 MB). Files go straight from the browser to S3,
  are split into chunks, embedded, and stored in Pinecone.
- **Access control**: every chunk carries an owner and allowed groups; search filters on them, so
  text a user may not read never reaches the model. Data is also separated per tenant.
- **Guardrails**: blocks prompt-injection style questions and answers that leak secrets.
- **Human-in-the-loop approvals**: sensitive requests (for example "delete ...") are held for an
  administrator instead of being answered.
- **Audit log and metrics** in Postgres.
- **Tracing** with LangSmith or Rius (switchable, records no document text by default).

## How it works

**Architecture** (plain, editable versions with their HTML source are in [`docs/diagrams/`](docs/diagrams)):

![Umbrella AI architecture](docs/Umbrella%20AI%20Architecture%20Diagram.png)

**Workflow** (upload, asking a question, and approvals):

![Umbrella AI workflow](docs/Umbrella%20AI%20Workflow%20Diagram.png)

The same question flow in text:

```
Ask a question
   |
   v
guard ---> retrieve ---> generate ---> validate ---> answer + sources
 |            |              |             |
 | blocks     | OpenAI       | Anthropic   | blocks leaked
 | attacks    | embedding,   | writes the  | secrets
 |            | Pinecone     | answer from |
 |            | search as    | the passages|
 |            | this user    |             |
 |            |
 |            +--- nothing relevant found ---> fixed "no evidence" reply (no model call)
```

Uploading a document: browser -> S3 -> `worker/ingest.py` (read, chunk, embed, store in Pinecone)
-> status `ready` in Postgres.

| Service | Role |
|---|---|
| OpenAI `text-embedding-3-large` (1024 dims) | Turns text into vectors |
| Anthropic `claude-haiku-5-5` | Writes the answers |
| Pinecone (serverless, cosine) | Vector search, one namespace per tenant |
| AWS S3 | Stores uploaded files |
| PostgreSQL | Documents, chat history, approvals, feedback, audit log |
| LangGraph | The question-answering pipeline |
| FastAPI + plain HTML/CSS/JS | Web app and interface |

## Quick start

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/bsivavenu/umbrella-enterprise-ai.git
cd umbrella-enterprise-ai
uv sync

cp .env.example .env        # then fill in your own keys (see below)
uv run scripts/init_db.py   # creates the database tables (run once)

uv run uvicorn app.main:app --reload
```

Open http://localhost:8000, go to **Documents**, upload the files in [`sample_docs/`](sample_docs),
then ask questions such as "How many vacation days do I get?".

### What you need in `.env`

| Group | Variables |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Pinecone | `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` (the app creates a 1024-dim cosine index if missing) |
| AWS S3 | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET` |
| Postgres | `DATABASE_URL` |
| Tracing (optional) | `OBSERVABILITY_PROVIDER`, `OBSERVABILITY_CAPTURE`, `LANGSMITH_API_KEY` or `RIUS_API_KEY` |
| Access (recommended when public) | `APP_PASSWORD`: one shared password, asked by the browser. Empty means no password |

The S3 bucket must allow `PUT` from the browser (a CORS rule), because uploads go straight to S3.
`.env.example` lists every setting with its default. Never commit `.env`.

### Useful settings

| Setting | Default | Meaning |
|---|---|---|
| `MIN_RELEVANCE` | `0.30` | Search hits scoring below this are ignored. Measured on the sample documents: unrelated questions score up to 0.23, real matches 0.33 and above |
| `MEMORY_TURNS` | `6` | How many past question/answer pairs are sent back to the model |
| `HITL_ENABLED` | `True` | Hold sensitive requests for approval |
| `MAX_UPLOAD_BYTES` | `10485760` | Upload size limit (10 MB) |
| `OBSERVABILITY_PROVIDER` | `langsmith` | `langsmith`, `rius` or `none` |
| `OBSERVABILITY_CAPTURE` | `metadata` | `off` (timing and errors), `metadata` (plus token counts), `full` (plus prompts and answers, use only with fake data) |

## CI and deployment

- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push to `main` and every
  pull request: it installs the locked dependencies, compiles the code, runs the tests that need no
  keys, then builds the Docker image and checks that it starts and answers. It uses no secrets.
- **Deployment** is on [Render](https://render.com). [`render.yaml`](render.yaml) describes the web
  service (built from the [`Dockerfile`](Dockerfile)), and Render redeploys only after the CI checks pass.

First-time setup on Render:

1. In the Render dashboard choose **New > Blueprint** and connect this GitHub repository.
2. Render reads `render.yaml` and asks for the secret values: `DATABASE_URL`, `OPENAI_API_KEY`,
   `ANTHROPIC_API_KEY`, `PINECONE_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `S3_BUCKET`, `LANGSMITH_API_KEY` and `APP_PASSWORD` (the shared password for the app: choose a
   long random one). They are stored in Render, never in git.
3. Apply. When the deploy finishes, open the service URL and check `/health/live`.
4. If the database is new, create the tables once: `uv run scripts/init_db.py` (see Quick start).

The service runs in the Oregon region, next to the database. The free plan sleeps when idle and
takes about a minute to wake; choose a paid plan in `render.yaml` for always-on.
The app asks for `APP_PASSWORD` before showing anything (any username, the shared password). Without
it, anyone with the service URL could use the app and your API credits, so always set it on a public URL.

Run the container locally:

```bash
docker build -t umbrella-ai .
docker run --rm -p 8000:8000 --env-file .env umbrella-ai
```

## Project layout

```
app/
  main.py            web API (FastAPI): upload, chat, history, feedback, approvals, metrics
  auth.py            optional shared-password gate (APP_PASSWORD)
  graph.py           the question-answering pipeline (LangGraph)
  aws.py             OpenAI embeddings, Anthropic answers, S3 client
  vector.py          Pinecone: store, search with access filter, delete
  memory.py          conversation history in Postgres
  guardrails.py      input and output checks
  db.py              Postgres connection pool and audit helper
  config.py          all settings, read from .env
  observability.py   LangSmith / Rius / off, with token counts
  demo_context.py    the fixed demo user (replace for real authentication)
  templates/, static/  the web interface
worker/ingest.py     file -> chunks -> embeddings -> Pinecone
infra/schema.sql     database tables
scripts/init_db.py   creates the tables
sample_docs/         fake documents for trying the app
test/                tests that run against the real services
docs/SETUP.md        detailed notes: what each file does, build order, known issues
Dockerfile           container image (non-root, no secrets baked in)
render.yaml          Render blueprint (web service, auto-deploy after CI passes)
.github/workflows/ci.yml   tests and Docker build on every push and pull request
```

## Tests

The tests call the real services (a few cents at most) under throwaway tenants and clean up after
themselves. Run any of them from the project root:

```bash
uv run test/test_config.py          # settings (no network)
uv run test/test_vector.py          # Pinecone: store, search, access rules, tenant isolation
uv run test/test_aws.py             # OpenAI embeddings, Anthropic answers, S3
uv run test/test_ingest.py          # S3 -> chunks -> Pinecone (add --keep to inspect, --cleanup to remove)
uv run test/test_graph.py           # guardrails and the full question-answering run
uv run test/test_memory.py          # conversation history in Postgres
uv run test/test_observability.py   # LangSmith / Rius / off switch
uv run test/test_main.py            # the whole web API end to end
uv run test/test_auth.py            # the shared-password gate (no keys needed)
```

`test/test.py` and `test/test_claude.py` try AWS Bedrock and are expected to fail on an account
without Bedrock access.

## Limitations

- **No user accounts.** Everyone is the same demo user (`app/demo_context.py`). The optional
  `APP_PASSWORD` is one shared password for the whole app (HTTP Basic), not per-user login. Real
  authentication means replacing `principal()` there; nothing else needs to change.
- **Simple guardrails.** Input and output checks are pattern matching. They stop obvious attacks,
  not clever ones. The real protection is the access filter in the search.
- **Approvals are a record only.** Approving a request does not run any action; the app has no
  destructive actions yet. The "needs approval" check is a keyword match.
- **No OCR.** Scanned PDFs without a text layer are rejected with a clear message.
- **Ingestion runs inside the upload request.** Fine for small files; large files would need a
  background worker.

## License

See [LICENSE](LICENSE).
