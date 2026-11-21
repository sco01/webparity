# SPDX-License-Identifier: BSD-2-Clause
import json
import socket
import threading

from webparity.bidi import BidiClient, WebSocket


def unmask(frame: bytes) -> bytes:
    n = frame[1] & 0x7F
    offset = 2
    if n == 126:
        n = int.from_bytes(frame[2:4], "big")
        offset = 4
    mask = frame[offset : offset + 4]
    data = frame[offset + 4 : offset + 4 + n]
    return bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def test_encode_frame_masks_payload():
    frame = WebSocket.encode_frame(b"hello", mask=b"\x01\x02\x03\x04")
    assert frame[0] == 0x81 and frame[1] == 0x85
    assert unmask(frame) == b"hello"


def test_encode_frame_extended_length():
    payload = b"x" * 300
    frame = WebSocket.encode_frame(payload, mask=b"\x00\x00\x00\x00")
    assert frame[1] & 0x7F == 126
    assert unmask(frame) == payload


def fake_bidi_server(server: socket.socket) -> None:
    """Accepts one client, completes the handshake, sends one event and answers one command."""
    conn, _ = server.accept()
    conn.recv(4096)  # the handshake request
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")

    def send(obj):
        data = json.dumps(obj).encode()
        conn.sendall(bytes([0x81, len(data)]) + data)  # server frames are not masked

    send({"type": "event", "method": "log.entryAdded", "params": {"text": "hi"}})
    frame = conn.recv(4096)
    command = json.loads(unmask(frame))
    send({"type": "success", "id": command["id"], "result": {"echo": command["method"]}})
    conn.recv(4096)
    conn.close()


def test_bidi_client_command_and_event():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    threading.Thread(target=fake_bidi_server, args=(server,), daemon=True).start()
    client = BidiClient(f"ws://127.0.0.1:{port}/session")
    assert client.command("session.status", timeout=5) == {"echo": "session.status"}
    assert client.events[0]["method"] == "log.entryAdded"
    client.close()
    server.close()
