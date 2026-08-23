#!/usr/bin/env python3
"""
Remote Assistance Relay Server
==============================

A lightweight asyncio WebSocket relay that brokers a remote-desktop session
between exactly one *host* (the end-user's machine, running the asset agent)
and one or more *viewers* (the Odoo administrator's browser).

It intentionally does NOT understand the payload. It moves bytes:

    host  --(binary video frames)-->  viewer(s)
    viewer --(json input/control)-->  host

Rooms are keyed by a session *token*. The token is a cryptographically random
value minted by Odoo when the admin requests a session AND the customer has
accepted the invitation. Only parties holding the token can join the room, so
the token is the capability. The relay can optionally verify the token against
Odoo (set ODOO_VERIFY_URL) so a leaked/expired token is rejected at connect
time as well.

Protocol
--------
First message from every client MUST be JSON:

    {"type": "hello", "role": "host" | "viewer", "token": "<session token>"}

After a successful hello the relay:
  * registers the client in the room for that token
  * tells the counterpart(s) that a peer joined  ({"type":"peer","event":"join","role":...})
Everything after hello is forwarded verbatim:
  * host   -> broadcast to all viewers in the room
  * viewer -> forwarded to the host in the room
Control messages the relay itself understands (still forwarded too):
  * {"type":"bye"}        graceful close, ends the room
  * {"type":"ping"}       -> relay replies {"type":"pong"} (keepalive)

Run
---
    pip install websockets
    python3 relay.py --host 0.0.0.0 --port 8765

For production put this behind a TLS-terminating reverse proxy (nginx/caddy)
so browsers connect over wss://, or pass --certfile/--keyfile to serve TLS
directly.
"""

import argparse
import asyncio
import json
import logging
import os
import ssl
import time
from collections import defaultdict

try:
    import websockets
except ImportError:
    raise SystemExit("Missing dependency: pip install websockets")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [relay] %(levelname)s %(message)s",
)
log = logging.getLogger("relay")

# Optional: URL Odoo exposes to confirm a token is still valid.
# The relay POSTs {"token": "..."} and expects {"valid": true}.
ODOO_VERIFY_URL = os.environ.get("ODOO_VERIFY_URL", "").strip()
SESSION_IDLE_TIMEOUT = int(os.environ.get("RELAY_IDLE_TIMEOUT", "1800"))  # 30 min


class Room:
    """One session. Holds a single host and any number of viewers."""

    __slots__ = ("token", "host", "viewers", "created", "last_activity")

    def __init__(self, token):
        self.token = token
        self.host = None                 # a single websocket
        self.viewers = set()             # set of websockets
        self.created = time.time()
        self.last_activity = time.time()

    def touch(self):
        self.last_activity = time.time()

    def is_empty(self):
        return self.host is None and not self.viewers


class Relay:
    def __init__(self):
        self.rooms = {}                          # token -> Room
        self.client_room = {}                    # websocket -> token
        self.client_role = {}                    # websocket -> role

    # -- token verification ------------------------------------------------
    async def verify_token(self, token):
        if not ODOO_VERIFY_URL:
            # No callback configured: trust the token's unguessability.
            return True
        try:
            # Local import so `websockets` install alone is enough to run.
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(ODOO_VERIFY_URL, json={"token": token}) as r:
                    if r.status != 200:
                        return False
                    data = await r.json()
                    return bool(data.get("valid"))
        except Exception as e:
            log.warning("token verify failed (%s); rejecting", e)
            return False

    # -- room helpers ------------------------------------------------------
    def get_room(self, token):
        room = self.rooms.get(token)
        if room is None:
            room = Room(token)
            self.rooms[token] = room
        return room

    def drop_client(self, ws):
        token = self.client_room.pop(ws, None)
        role = self.client_role.pop(ws, None)
        if token is None:
            return
        room = self.rooms.get(token)
        if not room:
            return
        if role == "host" and room.host is ws:
            room.host = None
        else:
            room.viewers.discard(ws)
        if room.is_empty():
            self.rooms.pop(token, None)
            log.info("room %s closed (empty)", token[:8])

    async def broadcast_to_viewers(self, room, message, is_binary):
        dead = []
        for v in list(room.viewers):
            try:
                await v.send(message)
            except Exception:
                dead.append(v)
        for v in dead:
            room.viewers.discard(v)

    async def send_to_host(self, room, message):
        if room.host is not None:
            try:
                await room.host.send(message)
            except Exception:
                pass

    async def notify(self, ws, obj):
        try:
            await ws.send(json.dumps(obj))
        except Exception:
            pass

    # -- main per-connection handler --------------------------------------
    async def handler(self, ws):
        peer = getattr(ws, "remote_address", ("?", 0))
        log.info("connection from %s", peer)
        try:
            # First frame must be the hello.
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            hello = json.loads(raw)
            if hello.get("type") != "hello":
                await self.notify(ws, {"type": "error", "reason": "expected hello"})
                return
            role = hello.get("role")
            token = hello.get("token")
            if role not in ("host", "viewer") or not token:
                await self.notify(ws, {"type": "error", "reason": "bad hello"})
                return

            if not await self.verify_token(token):
                await self.notify(ws, {"type": "error", "reason": "invalid token"})
                log.warning("rejected %s: invalid token", peer)
                return

            room = self.get_room(token)

            if role == "host":
                if room.host is not None:
                    await self.notify(ws, {"type": "error", "reason": "host already present"})
                    return
                room.host = ws
            else:
                room.viewers.add(ws)

            self.client_room[ws] = token
            self.client_role[ws] = role
            room.touch()
            log.info("room %s: %s joined (viewers=%d host=%s)",
                     token[:8], role, len(room.viewers), room.host is not None)

            await self.notify(ws, {"type": "welcome", "role": role})
            # Tell the counterpart a peer joined.
            if role == "viewer":
                await self.send_to_host(room, json.dumps(
                    {"type": "peer", "event": "join", "role": "viewer"}))
            else:
                await self.broadcast_to_viewers(room, json.dumps(
                    {"type": "peer", "event": "join", "role": "host"}), False)

            # Forwarding loop.
            async for message in ws:
                room.touch()
                is_binary = isinstance(message, (bytes, bytearray))

                # Peek at small text control messages.
                if not is_binary:
                    try:
                        obj = json.loads(message)
                        mtype = obj.get("type")
                    except Exception:
                        obj, mtype = None, None
                    if mtype == "ping":
                        await self.notify(ws, {"type": "pong"})
                        continue
                    if mtype == "bye":
                        break

                if role == "host":
                    await self.broadcast_to_viewers(room, message, is_binary)
                else:
                    await self.send_to_host(room, message)

        except asyncio.TimeoutError:
            await self.notify(ws, {"type": "error", "reason": "hello timeout"})
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            log.warning("handler error from %s: %s", peer, e)
        finally:
            token = self.client_room.get(ws)
            role = self.client_role.get(ws)
            self.drop_client(ws)
            if token and token in self.rooms:
                room = self.rooms[token]
                gone = {"type": "peer", "event": "leave", "role": role}
                if role == "host":
                    await self.broadcast_to_viewers(room, json.dumps(gone), False)
                else:
                    await self.send_to_host(room, json.dumps(gone))
            log.info("connection closed %s (role=%s)", peer, role)

    async def reaper(self):
        """Close idle rooms so a crashed peer can't pin a session forever."""
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for token, room in list(self.rooms.items()):
                if now - room.last_activity > SESSION_IDLE_TIMEOUT:
                    log.info("room %s idle-timeout", token[:8])
                    for ws in ([room.host] if room.host else []) + list(room.viewers):
                        try:
                            await ws.close(code=4000, reason="idle timeout")
                        except Exception:
                            pass
                    self.rooms.pop(token, None)


async def amain(args):
    relay = Relay()
    ssl_ctx = None
    if args.certfile and args.keyfile:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(args.certfile, args.keyfile)
        log.info("TLS enabled")

    asyncio.get_event_loop().create_task(relay.reaper())
    log.info("relay listening on %s:%s (idle timeout %ss, token verify=%s)",
             args.host, args.port, SESSION_IDLE_TIMEOUT, bool(ODOO_VERIFY_URL))
    async with websockets.serve(
        relay.handler, args.host, args.port,
        ssl=ssl_ctx, max_size=None, ping_interval=20, ping_timeout=20,
    ):
        await asyncio.Future()  # run forever


def main():
    ap = argparse.ArgumentParser(description="Remote Assistance WebSocket relay")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--certfile", default=os.environ.get("RELAY_CERTFILE"))
    ap.add_argument("--keyfile", default=os.environ.get("RELAY_KEYFILE"))
    args = ap.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
