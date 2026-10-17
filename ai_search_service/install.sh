#!/usr/bin/env bash
# One-time setup on a server: venv, CPU torch, packages, the embedding model, and the systemd service.
#   cd ~/asset_management/ai_search_service && bash install.sh
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
$PY -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install -r requirements.txt
# download the model now (needs internet once); afterwards it is read from ~/.cache/huggingface
venv/bin/python -c "from sentence_transformers import SentenceTransformer as S; S('${AI_SEARCH_MODEL:-BAAI/bge-small-en-v1.5}', device='cpu'); print('model ready')"
# systemd user service, started at boot
mkdir -p ~/.config/systemd/user
sed "s#__DIR__#$PWD#g" ai-search.service > ~/.config/systemd/user/ai-search.service
systemctl --user daemon-reload
systemctl --user enable --now ai-search.service
loginctl enable-linger "$USER" 2>/dev/null || echo "Run once as root so it starts without login: sudo loginctl enable-linger $USER"
sleep 5
curl -s http://localhost:${AI_SEARCH_PORT:-9010}/health && echo
