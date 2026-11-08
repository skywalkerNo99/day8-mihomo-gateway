"""Standalone TCP-only Muon (Day8 1.1.0) to SOCKS5 adapter.

No Day8 executable, host proxy, or macOS API is used at runtime.
"""
import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import secrets
import signal
import struct
import time
from pathlib import Path

from blake3 import blake3
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

LOG = logging.getLogger("muon")


def derive(context, material, size=16):
    return blake3(material, derive_key_context=context).digest(length=size)


class StreamCipher:
    def __init__(self, psk, salt):
        self.cipher = AESGCM(derive("shadowsocks 2022 session subkey", psk + salt, len(psk)))
        self.counter = 0

    def seal(self, data):
        out = self.cipher.encrypt(self.counter.to_bytes(12, "little"), data, None)
        self.counter += 1
        return out

    def open(self, data):
        out = self.cipher.decrypt(self.counter.to_bytes(12, "little"), data, None)
        self.counter += 1
        return out


def address(host, port):
    try:
        ip = ipaddress.ip_address(host)
        return bytes([1 if ip.version == 4 else 4]) + ip.packed + struct.pack("!H", port)
    except ValueError:
        name = host.encode("idna")
        if len(name) > 255:
            raise ValueError("destination name too long")
        return b"\x03" + bytes([len(name)]) + name + struct.pack("!H", port)


class Muon:
    def __init__(self, config):
        self.config = config
        size = {"aes-128-gcm": 16, "aes-256-gcm": 32}[config["cipher"]]
        self.server_psk = derive("nebrix-muon-server-psk", config["server_password"].encode(), size)
        self.user_psk = derive("nebrix-muon-user-psk", config["user_password"].encode(), size)
        self.size = size

    async def connect(self, host, port):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.config["server"], self.config["port"]), 10)
        salt = secrets.token_bytes(self.size)
        identity_key = derive("muon 2026 identity subkey", self.server_psk + salt, self.size)
        enc = Cipher(algorithms.AES(identity_key), modes.ECB()).encryptor()
        identity = enc.update(blake3(self.user_psk).digest()[:16]) + enc.finalize()
        tx = StreamCipher(self.user_psk, salt)
        max_padding = self.config.get("max_padding", 1024)
        padding = secrets.token_bytes(1 + secrets.randbelow(max_padding)) if max_padding else b""
        variable = address(host, port) + struct.pack("!H", len(padding)) + padding
        fixed = b"\x00" + struct.pack("!QH", int(time.time()), len(variable))
        writer.write(salt + identity + tx.seal(fixed) + tx.seal(variable))
        await writer.drain()
        return MuonStream(reader, writer, tx, self.user_psk, salt)


class MuonStream:
    def __init__(self, reader, writer, tx, psk, request_salt):
        self.reader, self.writer, self.tx = reader, writer, tx
        self.psk, self.request_salt = psk, request_salt
        self.rx = None
        self.pending_length = None

    async def receive(self):
        if self.rx is None:
            salt = await self.reader.readexactly(len(self.psk))
            self.rx = StreamCipher(self.psk, salt)
            header = self.rx.open(await self.reader.readexactly(11 + len(self.psk) + 16))
            if header[0] != 1:
                raise ValueError("invalid Muon response type")
            timestamp = struct.unpack("!Q", header[1:9])[0]
            if abs(time.time() - timestamp) > 900:
                raise ValueError("stale Muon response")
            if not secrets.compare_digest(header[9:9+len(self.psk)], self.request_salt):
                raise ValueError("Muon response request salt mismatch")
            self.pending_length = struct.unpack("!H", header[-2:])[0]
            # Muon may acknowledge the handshake without attaching a payload.
            # A zero-length header has no payload AEAD tag; the next bytes are
            # the first regular length chunk, using the next nonce.
            if self.pending_length == 0:
                self.pending_length = None
            LOG.info("authenticated Muon response")
        while True:
            if self.pending_length is None:
                raw = await self.reader.readexactly(18)
                self.pending_length = struct.unpack("!H", self.rx.open(raw))[0]
            n, self.pending_length = self.pending_length, None
            if n == 0:
                raise ValueError("invalid empty Muon data frame")
            data = self.rx.open(await self.reader.readexactly(n + 16))
            if data:
                return data

    async def send(self, data):
        for offset in range(0, len(data), 65535):
            chunk = data[offset:offset+65535]
            self.writer.write(self.tx.seal(struct.pack("!H", len(chunk))) + self.tx.seal(chunk))
        await self.writer.drain()

    async def close(self):
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


async def socks_address(reader, atyp):
    if atyp == 1:
        host = str(ipaddress.ip_address(await reader.readexactly(4)))
    elif atyp == 4:
        host = str(ipaddress.ip_address(await reader.readexactly(16)))
    elif atyp == 3:
        n = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(n)).decode("ascii")
    else:
        raise ValueError("invalid SOCKS address type")
    return host, struct.unpack("!H", await reader.readexactly(2))[0]


async def handle(reader, writer, muon):
    stream = None
    replied = False
    tasks = []
    try:
        version, count = await asyncio.wait_for(reader.readexactly(2), 10)
        methods = await reader.readexactly(count)
        if version != 5 or 0 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(b"\x05\x00")
        await writer.drain()
        version, command, reserved, atyp = await reader.readexactly(4)
        if version != 5 or command != 1 or reserved != 0:
            writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
            await writer.drain()
            return
        host, port = await socks_address(reader, atyp)
        stream = await muon.connect(host, port)
        writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
        await writer.drain()
        replied = True
        LOG.info("tunnel requested destination=%s:%d", host, port)

        async def upload():
            while data := await reader.read(65535):
                await stream.send(data)

        async def download():
            while True:
                data = await stream.receive()
                writer.write(data)
                await writer.drain()

        tasks = [asyncio.create_task(upload()), asyncio.create_task(download())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except asyncio.IncompleteReadError as exc:
        if exc.partial:
            LOG.warning("truncated encrypted stream bytes=%d", len(exc.partial))
    except Exception as exc:
        LOG.warning("tunnel failed error=%s", type(exc).__name__)
        if not replied:
            writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6)
            with contextlib.suppress(Exception):
                await writer.drain()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if stream:
            await stream.close()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def serve(config, stopping, host="127.0.0.1", port=17889, ready=None, current=None):
    """Serve native SOCKS5; the owner retains decrypted config only in memory."""
    muon = Muon(config)
    tasks = set()
    clients = set()

    def connected(reader, writer):
        clients.add(writer)
        task = asyncio.create_task(handle(reader, writer, current() if current else muon))
        tasks.add(task)
        def finished(done):
            tasks.discard(done)
            clients.discard(writer)
        task.add_done_callback(finished)

    server = await asyncio.start_server(connected, host, port)
    if ready:
        ready.set()
    LOG.info("native Muon SOCKS5 listening port=%d", port)
    try:
        await stopping.wait()
    finally:
        # Close active clients before waiting on the listener, including idle
        # clients blocked halfway through SOCKS negotiation.
        LOG.info("stopping Muon adapter active_clients=%d", len(clients))
        server.close()
        for writer in list(clients):
            writer.close()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 3)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server.wait_closed(), 3)


async def main():
    """Development entry point for synthetic protocol tests only."""
    config = json.loads(Path(os.environ["MUON_CONFIG"]).read_text())
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    await serve(config, stopping, port=int(os.environ.get("MUON_PORT", "1080")))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
