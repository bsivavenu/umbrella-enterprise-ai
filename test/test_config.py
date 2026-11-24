"""Tests for app/config.py (no network, no AWS calls).

Run with: uv run test/test_config.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings, get_settings  # noqa: E402

ENV_KEYS = [
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_BUCKET",
    "MAX_UPLOAD_BYTES", "BEDROCK_EMBED_MODEL", "BEDROCK_CHAT_MODEL", "EMBED_DIM",
    "PINECONE_API_KEY", "PINECONE_INDEX_NAME", "PINECONE_CLOUD", "PINECONE_REGION",
    "PINECONE_READY_TIMEOUT", "DATABASE_URL", "RIUS_API_KEY", "RIUS_SERVICE_NAME",
    "RIUS_DISABLED", "RIUS_CAPTURE_CONTENT",
]


def clean_env() -> dict:
    """Remove config vars from the process env; return them so they can be restored."""
    saved = {k: os.environ.pop(k) for k in ENV_KEYS if k in os.environ}
    return saved


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.aws_region == "us-east-1"
    assert s.max_upload_bytes == 10 * 1024 * 1024 == 10_485_760
    assert s.bedrock_embed_model == "amazon.titan-embed-text-v2:0"
    assert s.bedrock_chat_model == "amazon.nova-2-lite-v1:0"
    assert s.embed_dim == 1024
    assert s.pinecone_cloud == "aws" and s.pinecone_region == "us-east-1"
    assert s.pinecone_ready_timeout == 180
    assert s.rius_service_name == "umbrella_ai"
    assert s.rius_disabled is False and s.rius_capture_content is False
    assert s.s3_bucket == "" and s.database_url == "" and s.pinecone_api_key == ""


def test_env_overrides_and_type_coercion() -> None:
    os.environ.update({
        "MAX_UPLOAD_BYTES": "2048",
        "EMBED_DIM": "512",
        "RIUS_DISABLED": "true",
        "RIUS_CAPTURE_CONTENT": "1",
        "PINECONE_INDEX_NAME": "my-index",
        "AWS_REGION": "eu-west-1",
    })
    s = Settings(_env_file=None)
    assert s.max_upload_bytes == 2048 and isinstance(s.max_upload_bytes, int)
    assert s.embed_dim == 512
    assert s.rius_disabled is True and s.rius_capture_content is True
    assert s.pinecone_index_name == "my-index"
    assert s.aws_region == "eu-west-1"


def test_invalid_value_rejected() -> None:
    os.environ["MAX_UPLOAD_BYTES"] = "ten-megabytes"
    try:
        Settings(_env_file=None)
    except Exception as exc:  # pydantic ValidationError
        assert "max_upload_bytes" in str(exc)
    else:
        raise AssertionError("non-numeric MAX_UPLOAD_BYTES should be rejected")


def test_old_index_host_name_is_ignored() -> None:
    os.environ["PINECONE_INDEX_HOST"] = "stale-name"
    try:
        s = Settings(_env_file=None)
        assert s.pinecone_index_name == "", "old PINECONE_INDEX_HOST must not be read"
        assert not hasattr(s, "pinecone_index_host")
    finally:
        os.environ.pop("PINECONE_INDEX_HOST", None)


def test_loads_dotenv_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "S3_BUCKET=file-bucket\nMAX_UPLOAD_BYTES=999\nUNKNOWN_VAR=ignored\n"
    )
    s = Settings(_env_file=env_file)
    assert s.s3_bucket == "file-bucket"
    assert s.max_upload_bytes == 999


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()


def test_real_dotenv_loads_expected_keys() -> None:
    """The project's real .env loads with sane values (secrets are not printed)."""
    get_settings.cache_clear()
    s = get_settings()
    assert s.aws_region, "AWS_REGION missing (check it isn't spelled AWS-REGION)"
    assert s.s3_bucket and s.database_url and s.pinecone_api_key
    assert s.pinecone_index_name, "PINECONE_INDEX_NAME missing in .env"
    assert s.embed_dim == 1024


def main() -> int:
    import tempfile

    tests = [
        test_defaults,
        test_env_overrides_and_type_coercion,
        test_invalid_value_rejected,
        test_old_index_host_name_is_ignored,
        test_loads_dotenv_file,
        test_get_settings_is_cached,
    ]
    failures = 0
    saved = clean_env()
    try:
        for test in tests:
            for k in ENV_KEYS:
                os.environ.pop(k, None)
            try:
                if test is test_loads_dotenv_file:
                    with tempfile.TemporaryDirectory() as d:
                        test(Path(d))
                else:
                    test()
                print(f"[PASS] {test.__name__}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    finally:
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(saved)

    # Runs last, against the real .env, with the original environment restored.
    try:
        test_real_dotenv_loads_expected_keys()
        print("[PASS] test_real_dotenv_loads_expected_keys")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"[FAIL] test_real_dotenv_loads_expected_keys: "
              f"{type(exc).__name__}: {exc}")

    total = len(tests) + 1
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
