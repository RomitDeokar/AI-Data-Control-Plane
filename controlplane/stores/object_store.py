"""MinIO / S3 object store adapter.

Data zones:
  raw/        — untouched source files exactly as ingested
  staged/     — validated + enriched + embedded JSONL, keyed by version_id
  quarantine/ — failed records with reasons
  artifacts/  — reports (validation, quality, promotion) for the UI + audits
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

from minio import Minio

from controlplane.config import settings

logger = logging.getLogger(__name__)


class ObjectStore:
    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool = False,
    ):
        self.client = Minio(
            endpoint or settings.minio_endpoint,
            access_key=access_key or settings.minio_access_key,
            secret_key=secret_key or settings.minio_secret_key,
            secure=secure,
        )
        # Remember buckets we've already ensured this process so we don't do a
        # bucket_exists round-trip before every single put.
        self._ensured: set[str] = set()

    # ------------------------------------------------------------------ basics
    def ensure_bucket(self, bucket: str) -> None:
        if bucket in self._ensured:
            return
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
        self._ensured.add(bucket)

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> str:
        self.ensure_bucket(bucket)
        self.client.put_object(
            bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        uri = f"s3://{bucket}/{key}"
        logger.info("wrote %s (%d bytes)", uri, len(data))
        return uri

    def get_bytes(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        return [obj.object_name for obj in self.client.list_objects(bucket, prefix=prefix, recursive=True)]

    # -------------------------------------------------------------- JSON helpers
    def put_json(self, bucket: str, key: str, payload: Any) -> str:
        data = json.dumps(payload, indent=2, default=str).encode()
        return self.put_bytes(bucket, key, data, "application/json")

    def get_json(self, bucket: str, key: str) -> Any:
        return json.loads(self.get_bytes(bucket, key))

    def put_jsonl(self, bucket: str, key: str, records: list[dict[str, Any]]) -> str:
        lines = "\n".join(json.dumps(r, default=str) for r in records)
        return self.put_bytes(bucket, key, lines.encode(), "application/x-ndjson")

    def get_jsonl(self, bucket: str, key: str) -> list[dict[str, Any]]:
        raw = self.get_bytes(bucket, key).decode()
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    # ----------------------------------------------------------- zone shortcuts
    def write_raw(self, dataset: str, filename: str, data: bytes, content_type: str) -> str:
        return self.put_bytes(settings.bucket_raw, f"{dataset}/{filename}", data, content_type)

    def write_staged(self, version_id: str, records: list[dict[str, Any]]) -> str:
        return self.put_jsonl(settings.bucket_staged, f"{version_id}/records.jsonl", records)

    def write_quarantine(self, version_id: str, items: list[dict[str, Any]]) -> str:
        return self.put_jsonl(settings.bucket_quarantine, f"{version_id}/failed.jsonl", items)

    def write_artifact(self, version_id: str, name: str, payload: Any) -> str:
        return self.put_json(settings.bucket_artifacts, f"{version_id}/{name}.json", payload)
