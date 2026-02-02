from __future__ import annotations

import os
from dataclasses import dataclass

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError


@dataclass(frozen=True, slots=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str

    # Optional envs (kept for compatibility / future use)
    api_key_token: str | None = None
    base_url: str | None = None

    @property
    def endpoint_url(self) -> str:
        # Cloudflare R2 is S3-compatible.
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def load_r2_config() -> R2Config:
    account_id = (os.getenv("R2_ACCOUNT_ID") or "").strip()
    access_key_id = (os.getenv("ACCESS_KEY_ID") or "").strip()
    secret_access_key = (os.getenv("SECRET_ACCESS_KEY") or "").strip()

    if not account_id:
        raise RuntimeError("R2_ACCOUNT_ID is not set")
    if not access_key_id:
        raise RuntimeError("ACCESS_KEY_ID is not set")
    if not secret_access_key:
        raise RuntimeError("SECRET_ACCESS_KEY is not set")

    return R2Config(
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        api_key_token=(os.getenv("R2_API_KEY_TOKEN") or "").strip() or None,
        base_url=(os.getenv("CLOUDFLARE_R2_BASE_URL") or "").strip() or None,
    )


def get_r2_client(cfg: R2Config | None = None) -> BaseClient:
    if cfg is None:
        cfg = load_r2_config()

    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name="auto",
    )


def r2_object_exists(bucket: str, key: str, client: BaseClient | None = None) -> bool:
    if client is None:
        client = get_r2_client()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def r2_delete_object(bucket: str, key: str, client: BaseClient | None = None) -> None:
    if client is None:
        client = get_r2_client()
    client.delete_object(Bucket=bucket, Key=key)


def r2_download_to_path(bucket: str, key: str, dest_path: str, client: BaseClient | None = None) -> None:
    if client is None:
        client = get_r2_client()
    with open(dest_path, "wb") as f:
        client.download_fileobj(bucket, key, f)


def r2_presigned_get_url(
    bucket: str, key: str, expires_in: int = 3600, client: BaseClient | None = None
) -> str:
    if client is None:
        client = get_r2_client()
    return client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=int(expires_in),
    )

