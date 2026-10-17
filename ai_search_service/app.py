"""
AI Search Service: meaning (vector) search for any Odoo module.

One small Flask app: ChromaDB for storage, a sentence-transformers model for embeddings
(BAAI/bge-small-en-v1.5 by default, the same as the ERP AI chatbot). Each module keeps its
own collection, e.g. the DMS uses "dms_documents". Odoo stays the source of truth and checks
access; this service only answers "which items mean the closest to this text".

Generic API (JSON):
    GET  /health                                   service + model + collections
    POST /v1/<collection>/upsert   {doc_id, text, title?, code?, type?, folder?, rev_id?}
                                   replaces every chunk of that item
    POST /v1/<collection>/delete   {doc_id} | {doc_ids}
    POST /v1/<collection>/sync     {items: [...]}   replaces the whole collection
    POST /v1/<collection>/search   {query, k?, doc_ids?}
    GET  /v1/<collection>/health

DMS compatibility (what the dms module calls, collection "dms_documents"):
    /dms/index/upsert  /dms/index/delete  /dms/index/sync  /dms/search  /dms/health

Settings (environment variables):
    AI_SEARCH_HOST=0.0.0.0  AI_SEARCH_PORT=9010
    AI_SEARCH_DATA=<folder>/data            ChromaDB files
    AI_SEARCH_MODEL=BAAI/bge-small-en-v1.5  embedding model (HuggingFace name or local path)
    AI_SEARCH_THREADS=4  AI_SEARCH_BATCH=8  CPU threads and embedding batch size
    AI_SEARCH_TOKEN=                        optional: required as "Authorization: Bearer <token>"
"""
# Old sqlite on some Ubuntu releases: use pysqlite3 when it is installed
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules['pysqlite3']
except ImportError:
    pass

import logging
import os
import re
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, request

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv('AI_SEARCH_DATA', BASE / 'data'))
MODEL_NAME = os.getenv('AI_SEARCH_MODEL', 'BAAI/bge-small-en-v1.5')
THREADS = int(os.getenv('AI_SEARCH_THREADS', '4'))
BATCH = int(os.getenv('AI_SEARCH_BATCH', '8'))
TOKEN = os.getenv('AI_SEARCH_TOKEN', '')

CHUNK_CHARS = 1100      # bge-small reads ~512 tokens; leave room for the title line
OVERLAP_CHARS = 180
MAX_CHUNKS = 40
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_]{2,60}$')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
_log = logging.getLogger('ai_search')

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

_lock = threading.Lock()
_model = None
_client = None
_colls = {}


# ---------------------------------------------------------------- model and storage (loaded on first use)
def model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                try:
                    import torch
                    torch.set_num_threads(THREADS)
                except Exception:  # noqa: BLE001
                    pass
                t0 = time.time()
                _log.info("Loading embedding model %s ...", MODEL_NAME)
                _model = SentenceTransformer(MODEL_NAME, device='cpu')
                _log.info("Model ready in %.1fs", time.time() - t0)
    return _model


def query_prefix():
    """BGE English models retrieve better with this instruction on the query (not on the documents)."""
    m = MODEL_NAME.lower()
    return 'Represent this sentence for searching relevant passages: ' if 'bge' in m and 'en' in m else ''


def embed(texts):
    out = []
    for i in range(0, len(texts), BATCH):
        vecs = model().encode(texts[i:i + BATCH], normalize_embeddings=True, show_progress_bar=False, batch_size=BATCH)
        out.extend(vecs.tolist())
    return out


def collection(name):
    if not NAME_RE.match(name or ''):
        abort(400, description="collection: lowercase letters, digits and _ only")
    if name not in _colls:
        with _lock:
            global _client
            if _client is None:
                import chromadb
                DATA.mkdir(parents=True, exist_ok=True)
                _client = chromadb.PersistentClient(path=str(DATA))
            if name not in _colls:
                _colls[name] = _client.get_or_create_collection(
                    name=name, metadata={'hnsw:space': 'cosine', 'embed_model': MODEL_NAME})
    return _colls[name]


# ---------------------------------------------------------------- helpers
def chunks(text):
    text = re.sub(r'[ \t]+', ' ', text or '')
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        return []
    out, start = [], 0
    while start < len(text) and len(out) < MAX_CHUNKS:
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            cut = max(text.rfind('\n', start + CHUNK_CHARS // 2, end), text.rfind('. ', start + CHUNK_CHARS // 2, end))
            if cut > start:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return [c for c in out if c]


def meta(item, i):
    # Chroma metadata values must be str/int/float/bool, never None
    return {
        'doc_id': int(item.get('doc_id') or 0), 'rev_id': int(item.get('rev_id') or 0),
        'code': str(item.get('code') or '')[:60], 'title': str(item.get('title') or '')[:200],
        'type': str(item.get('type') or '')[:80], 'folder': str(item.get('folder') or '')[:200], 'chunk': i,
    }


def upsert(coll, items):
    total = 0
    for item in items:
        doc_id = int(item.get('doc_id') or 0)
        if not doc_id:
            continue
        coll.delete(where={'doc_id': doc_id})
        head = ' · '.join(filter(None, [item.get('code'), item.get('title'), item.get('type'), item.get('folder')]))
        parts = chunks(item.get('text') or '') or [head or str(doc_id)]
        texts = ['%s\n%s' % (head, c) if head else c for c in parts]
        coll.add(ids=['%s:%s' % (doc_id, i) for i in range(len(texts))], documents=texts,
                 embeddings=embed(texts), metadatas=[meta(item, i) for i in range(len(texts))])
        total += len(texts)
    return total


def body():
    return request.get_json(force=True, silent=True) or {}


@app.before_request
def check_token():
    if TOKEN and request.path != '/health' and request.headers.get('Authorization') != 'Bearer ' + TOKEN:
        abort(401)


@app.errorhandler(400)
@app.errorhandler(401)
def bad(e):
    return jsonify({'error': getattr(e, 'description', str(e))}), e.code


# ---------------------------------------------------------------- generic API
@app.route('/health')
def health():
    names = []
    try:
        collection('dms_documents')  # opens the store
        names = [c.name if hasattr(c, 'name') else str(c) for c in _client.list_collections()]
    except Exception as e:  # noqa: BLE001
        return jsonify({'status': 'error', 'error': str(e)}), 500
    return jsonify({'status': 'ok', 'service': 'ai_search_service', 'embed_model': MODEL_NAME,
                    'model_loaded': _model is not None, 'data': str(DATA), 'collections': names})


@app.route('/v1/<name>/health')
def coll_health(name):
    coll = collection(name)
    docs = {m['doc_id'] for m in (coll.get(include=['metadatas'])['metadatas'] or [])}
    return jsonify({'status': 'ok', 'collection': name, 'chunks': coll.count(), 'documents': len(docs),
                    'embed_model': MODEL_NAME})


@app.route('/v1/<name>/upsert', methods=['POST'])
def coll_upsert(name):
    data = body()
    if not data.get('doc_id'):
        abort(400, description="'doc_id' is required")
    return jsonify({'chunks': upsert(collection(name), [data])})


@app.route('/v1/<name>/delete', methods=['POST'])
def coll_delete(name):
    data = body()
    ids = [int(i) for i in (data.get('doc_ids') or [data.get('doc_id')]) if i]
    coll = collection(name)
    for doc_id in ids:
        coll.delete(where={'doc_id': doc_id})
    return jsonify({'deleted': ids})


@app.route('/v1/<name>/sync', methods=['POST'])
def coll_sync(name):
    items = body().get('items') or []
    coll = collection(name)
    existing = coll.get(include=[])['ids']
    if existing:
        coll.delete(ids=existing)
    return jsonify({'documents': len(items), 'chunks': upsert(coll, items)})


@app.route('/v1/<name>/search', methods=['POST'])
def coll_search(name):
    data = body()
    query = (data.get('query') or '').strip()
    if not query:
        abort(400, description="'query' is required")
    k = max(1, min(int(data.get('k') or 12), 50))
    coll = collection(name)
    total = coll.count()
    if not total:
        return jsonify({'results': []})
    where = {'doc_id': {'$in': [int(i) for i in data['doc_ids']]}} if data.get('doc_ids') else None
    vec = embed([query_prefix() + query[:2000]])[0]
    res = coll.query(query_embeddings=[vec], n_results=min(total, k), where=where,
                     include=['metadatas', 'distances', 'documents'])
    out = [{'id': rid, 'score': round(1 - dist, 4), 'text': doc, **m}
           for rid, m, dist, doc in zip(res['ids'][0], res['metadatas'][0], res['distances'][0], res['documents'][0])]
    return jsonify({'results': out})


# ---------------------------------------------------------------- DMS paths (the dms module calls these)
DMS = 'dms_documents'
app.add_url_rule('/dms/health', 'dms_health', lambda: coll_health(DMS))
app.add_url_rule('/dms/index/upsert', 'dms_upsert', lambda: coll_upsert(DMS), methods=['POST'])
app.add_url_rule('/dms/index/delete', 'dms_delete', lambda: coll_delete(DMS), methods=['POST'])
app.add_url_rule('/dms/index/sync', 'dms_sync', lambda: coll_sync(DMS), methods=['POST'])
app.add_url_rule('/dms/search', 'dms_search', lambda: coll_search(DMS), methods=['POST'])


if __name__ == '__main__':
    host = os.getenv('AI_SEARCH_HOST', '0.0.0.0')
    port = int(os.getenv('AI_SEARCH_PORT', '9010'))
    _log.info("AI Search Service on %s:%s, data in %s", host, port, DATA)
    app.run(host=host, port=port, debug=False, threaded=True)
