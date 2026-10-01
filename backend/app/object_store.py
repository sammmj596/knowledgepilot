from __future__ import annotations

import os
import boto3
from botocore.exceptions import ClientError

S3_BUCKET = os.environ.get("S3_BUCKET", "rag-docs-samjiang")
_s3 = boto3.client("s3", region_name=os.environ.get("S3_REGION", "us-east-1"))


def upload_bytes(data: bytes, *, key: str) -> None:
    """Upload raw bytes to S3 under the given key."""
    _s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data)


def download_bytes(key: str) -> bytes:
    """Fetch an object's bytes from S3."""
    try:
        obj = _s3.get_object(Bucket=S3_BUCKET, Key=key)
        return obj["Body"].read()
    except ClientError as e:
        raise FileNotFoundError(f"S3 object not found: {key}") from e


def delete_object(key: str) -> None:
    _s3.delete_object(Bucket=S3_BUCKET, Key=key)
