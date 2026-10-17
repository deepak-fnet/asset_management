# AI Search Service

Meaning (vector) search for Odoo modules. Flask + ChromaDB + `BAAI/bge-small-en-v1.5`
(the same embedding model as the ERP AI chatbot). Each module uses its own collection;
the DMS uses `dms_documents`. Odoo checks access, this service only finds what is close.

## Install on a server (once)
    cd ~/asset_management/ai_search_service
    bash install.sh            # venv, CPU torch, packages, model download, systemd service on port 9010

No internet on the server? Copy the model from a machine that has it:
    scp -r ~/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5 user@server:~/.cache/huggingface/hub/

## Connect the DMS
Odoo > DMS > Configuration > Settings > Ask DMS (meaning search):
- Meaning search service: `http://localhost:9010`
- press **Rebuild meaning search** (indexes every document)

## Run / check
    systemctl --user status ai-search
    systemctl --user restart ai-search
    curl localhost:9010/health
    curl localhost:9010/v1/dms_documents/health
    tail -f ai_search.log

## API
| Method | Path | Body |
|---|---|---|
| GET | /health | |
| POST | /v1/<collection>/upsert | {doc_id, text, title?, code?, type?, folder?, rev_id?} |
| POST | /v1/<collection>/delete | {doc_id} or {doc_ids} |
| POST | /v1/<collection>/sync | {items: [...]} replaces the collection |
| POST | /v1/<collection>/search | {query, k?, doc_ids?} |
The DMS paths /dms/index/upsert, /dms/index/delete, /dms/index/sync, /dms/search, /dms/health
are the same calls on the `dms_documents` collection.

## Settings (environment, in ai-search.service)
AI_SEARCH_PORT (9010), AI_SEARCH_HOST (0.0.0.0), AI_SEARCH_DATA (./data), AI_SEARCH_MODEL,
AI_SEARCH_THREADS (4), AI_SEARCH_BATCH (8), AI_SEARCH_TOKEN (optional bearer token; set the same
token in the DMS settings).
