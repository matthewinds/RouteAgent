"""Atomic disk cache; values contain public map data, never credentials."""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class JsonCache:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, namespace, arguments):
        key = json.dumps([namespace, arguments], sort_keys=True, ensure_ascii=False)
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, namespace, arguments, ttl=86400):
        path = self.path(namespace, arguments)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload["value"] if time.time() - payload["created"] < ttl else None
        except (FileNotFoundError, ValueError, KeyError):
            return None

    def put(self, namespace, arguments, value):
        path = self.path(namespace, arguments)
        fd, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"created": time.time(), "value": value}, stream, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class EvidenceStore:
    """Content-addressed raw responses, kept so any saved result can be audited later.

    Unlike JsonCache (keyed by request, overwritten by newer responses), an entry
    is named by the SHA-256 of its payload and is never replaced. Request headers
    and credentials are not stored.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, digest):
        return self.directory / digest[:2] / (digest + ".json.gz")

    def put(self, digest, source, retrieved_at, payload):
        import gzip
        path = self.path(digest)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as stream:
                stream.write(json.dumps({"source": source, "retrieved_at": retrieved_at, "payload": payload},
                                        ensure_ascii=False, default=str).encode())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, digest):
        import gzip
        try:
            with gzip.open(self.path(digest), "rb") as stream:
                return json.loads(stream.read())
        except (FileNotFoundError, OSError, ValueError):
            return None
