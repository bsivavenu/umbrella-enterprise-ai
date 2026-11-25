"""Ingestion worker: turns an uploaded file in S3 into searchable vectors in Pinecone.

Flow for one file (process_s3_key):
  1. lock the documents row and mark it 'processing'
  2. download the file from S3 (size and PDF checks)
  3. parse it into pages of text (pdf / docx / txt)
  4. cut the text into overlapping chunks
  5. embed each chunk (OpenAI) and upsert the vectors (Pinecone)
  6. mark the row 'ready' (or 'failed' on any error) and write an audit event
"""
import io
import logging
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.aws import client, embed
from app.config import settings
from app.db import audit, connection
from app.observability import initialize, trace
from app.vector import upsert

initialize()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('umbrella.worker')


def parse(blob: bytes, suffix: str):
    """Return a list of (page_number, text). Only .pdf, .docx and plain text are supported."""
    if suffix == '.pdf':
        reader = PdfReader(io.BytesIO(blob))
        return [(i + 1, page.extract_text() or '') for i, page in enumerate(reader.pages)]
    if suffix == '.docx':
        doc = Document(io.BytesIO(blob))
        return [(1, '\n'.join(p.text for p in doc.paragraphs))]
    return [(1, blob.decode('utf-8'))]


def chunks(text, size=1200, overlap=180):
    """Yield pieces of `size` characters; each piece repeats the last `overlap` characters of
    the previous one so a sentence cut at the boundary still appears whole in one chunk."""
    text = ' '.join(text.split())  # collapse all whitespace
    pos = 0
    while pos < len(text):
        yield text[pos:pos + size]
        if pos + size >= len(text):
            break
        pos += size - overlap


@trace("process_s3_key")
def process_s3_key(key):
    s = settings()

    # Step 1: claim the document. FOR UPDATE locks the row so two workers can't ingest it twice.
    with connection() as conn:
        doc = conn.execute('SELECT * FROM documents WHERE s3_key=%s FOR UPDATE', (key,)).fetchone()
        if not doc or doc['status'] == 'ready':
            return
        if doc['status'] not in ('pending', 'failed'):
            return  # already 'processing' elsewhere
        conn.execute("UPDATE documents SET status='processing' WHERE id=%s", (doc['id'],))

    try:
        # Step 2: download and sanity-check the file.
        obj = client('s3').get_object(Bucket=s.s3_bucket, Key=key)
        if obj['ContentLength'] > s.max_upload_bytes:
            raise ValueError('Upload too large')
        blob = obj['Body'].read(s.max_upload_bytes + 1)
        if len(blob) > s.max_upload_bytes:
            raise ValueError('Upload too large')
        suffix = Path(key).suffix.lower()
        if suffix == '.pdf' and not blob.startswith(b'%PDF'):
            raise ValueError('Invalid PDF')

        # Steps 3-4: parse into pages, then chunk each page.
        pages = parse(blob, suffix)
        parts = [(page, part) for page, text in pages for part in chunks(text)]
        if not parts:
            raise ValueError('No extractable text; scanned PDFs need OCR, which is not set up')

        # Step 5: one vector per chunk. The metadata carries the text and the ACL fields
        # (owner_sub, allowed_groups) that vector.search() filters on.
        vectors = []
        for i, (page, part) in enumerate(parts):
            vectors.append({
                'id': f'{doc["id"]}:{doc["current_version"]}:{i}',
                'values': embed(part),
                'metadata': {'document_id': str(doc['id']), 'title': doc['title'],
                             'page': page, 'text': part,
                             'owner_sub': doc['owner_sub'],
                             'allowed_groups': doc['allowed_groups']},
            })
        # Small batches keep each Pinecone request under its size limits.
        for i in range(0, len(vectors), 50):
            upsert(doc['tenant_id'], vectors[i:i + 50])

        # Step 6: success.
        with connection() as conn:
            conn.execute("UPDATE documents SET status='ready' WHERE id=%s", (doc['id'],))
            audit(conn, doc['owner_sub'], doc['tenant_id'], 'ingest_ready',
                  {'document_id': str(doc['id']), 'chunks': len(parts)})
        log.info('Ingested document %s chunks=%d', doc['id'], len(parts))
    except Exception:
        log.exception('Ingestion failed for key %s', key)
        with connection() as conn:
            conn.execute("UPDATE documents SET status='failed' WHERE id=%s", (doc['id'],))
        raise
