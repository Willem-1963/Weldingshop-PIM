import hashlib
import json

import pytest

from app.shopify_backup import (
    _media_urls,
    create_shopify_catalog_backup,
    list_shopify_catalog_backups,
    verify_shopify_catalog_backup,
)


def test_media_urls_are_unique_and_include_video_sources(tmp_path):
    export = tmp_path / "products.jsonl"
    export.write_text(
        "\n".join([
            json.dumps({"image": {"url": "https://cdn.example/a.jpg"}}),
            json.dumps({"image": {"url": "https://cdn.example/a.jpg"}}),
            json.dumps({"sources": [{"url": "https://cdn.example/movie.mp4"}]}),
        ]),
        encoding="utf-8",
    )
    assert _media_urls(export) == [
        "https://cdn.example/a.jpg",
        "https://cdn.example/movie.mp4",
    ]


def test_list_and_verify_shopify_catalog_backup(tmp_path):
    archive = tmp_path / "weldingshop-shopify-catalogus-20260825T120000Z.tar.gz.enc"
    archive.write_bytes(b"encrypted backup")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = tmp_path / "weldingshop-shopify-catalogus-20260825T120000Z.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    backups = list_shopify_catalog_backups(tmp_path)
    assert backups[0]["name"] == archive.name
    assert verify_shopify_catalog_backup(archive, backup_dir=tmp_path)["valid"]

    archive.write_bytes(b"changed")
    assert not verify_shopify_catalog_backup(archive, backup_dir=tmp_path)["valid"]


def test_catalog_backup_rejects_short_password(tmp_path):
    with pytest.raises(ValueError, match="minimaal 12"):
        create_shopify_catalog_backup("te-kort", backup_dir=tmp_path)
