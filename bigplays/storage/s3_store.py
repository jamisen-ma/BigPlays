from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import boto3


class S3Store:
    def __init__(self, bucket: str, prefix: str) -> None:
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        self.s3 = boto3.client("s3")

    def _key(self, local_path: Path) -> str:
        return self.prefix + local_path.name

    def upload_file(self, local_path: Path, extra_metadata: Optional[dict] = None) -> str:
        key = self._key(local_path)
        extra_args = {"Metadata": {}} if extra_metadata else None
        if extra_metadata:
            # S3 metadata values must be strings
            extra_args = {"Metadata": {k: str(v) for k, v in extra_metadata.items()}}
        self.s3.upload_file(str(local_path), self.bucket, key, ExtraArgs=extra_args or {})
        return f"s3://{self.bucket}/{key}"

    def upload_json(self, json_path: Path) -> str:
        key = self._key(json_path)
        self.s3.upload_file(str(json_path), self.bucket, key, ExtraArgs={"ContentType": "application/json"})
        return f"s3://{self.bucket}/{key}"

