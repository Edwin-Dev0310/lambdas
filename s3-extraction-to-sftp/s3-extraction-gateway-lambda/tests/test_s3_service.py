"""Tests unitarios para S3Service (usando moto para simular AWS)."""

from __future__ import annotations

import logging

import boto3
import pytest
from moto import mock_aws

from errors import NotFoundError
from id_codec import decode_key
from s3_service import S3Service

_BUCKET = "bucket-de-prueba"
_REGION = "us-east-1"


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("test.s3_service")
    log.addHandler(logging.NullHandler())
    return log


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name=_REGION)
        client.create_bucket(Bucket=_BUCKET)
        yield client


def test_list_pending_files_filtra_por_prefijo(s3_bucket, logger: logging.Logger) -> None:
    s3_bucket.put_object(Bucket=_BUCKET, Key="fractar/unprocessed/", Body=b"")
    s3_bucket.put_object(
        Bucket=_BUCKET,
        Key="fractar/unprocessed/2026/07/15/xml/factura001.xml",
        Body=b"<xml/>",
    )
    s3_bucket.put_object(Bucket=_BUCKET, Key="otro-prefijo/archivo.xml", Body=b"<xml/>")

    service = S3Service(_BUCKET, logger)
    files = service.list_pending_files("fractar/unprocessed/", url_expiration_seconds=300)

    assert len(files) == 1
    file_info = files[0]
    assert file_info["key"] == "fractar/unprocessed/2026/07/15/xml/factura001.xml"
    assert file_info["relativePath"] == "2026/07/15/xml/factura001.xml"
    assert file_info["fileName"] == "factura001.xml"
    assert file_info["size"] == len(b"<xml/>")
    assert file_info["lastModified"].endswith("Z")
    assert file_info["downloadUrl"].startswith("https://")

    # El 'id' debe ser reversible a la key original, sin estado adicional.
    assert decode_key(file_info["id"]) == file_info["key"]


def test_list_pending_files_excluye_marcador_de_carpeta(s3_bucket, logger: logging.Logger) -> None:
    s3_bucket.put_object(Bucket=_BUCKET, Key="fractar/unprocessed/", Body=b"")

    service = S3Service(_BUCKET, logger)
    files = service.list_pending_files("fractar/unprocessed/", url_expiration_seconds=300)

    assert files == []


def test_list_pending_files_orden_ascendente_por_fecha(s3_bucket, logger: logging.Logger) -> None:
    s3_bucket.put_object(Bucket=_BUCKET, Key="fractar/unprocessed/b.xml", Body=b"b")
    s3_bucket.put_object(Bucket=_BUCKET, Key="fractar/unprocessed/a.xml", Body=b"a")

    service = S3Service(_BUCKET, logger)
    files = service.list_pending_files("fractar/unprocessed/", url_expiration_seconds=300)

    last_modified_values = [f["lastModified"] for f in files]
    assert last_modified_values == sorted(last_modified_values)


def test_object_exists(s3_bucket, logger: logging.Logger) -> None:
    s3_bucket.put_object(Bucket=_BUCKET, Key="a.xml", Body=b"data")
    service = S3Service(_BUCKET, logger)

    assert service.object_exists("a.xml") is True
    assert service.object_exists("no_existe.xml") is False


def test_move_object_ok(s3_bucket, logger: logging.Logger) -> None:
    s3_bucket.put_object(
        Bucket=_BUCKET, Key="fractar/unprocessed/2026/07/15/factura001.xml", Body=b"<xml/>"
    )
    service = S3Service(_BUCKET, logger)

    service.move_object(
        "fractar/unprocessed/2026/07/15/factura001.xml",
        "processed/2026/07/15/factura001.xml",
    )

    assert service.object_exists("fractar/unprocessed/2026/07/15/factura001.xml") is False
    assert service.object_exists("processed/2026/07/15/factura001.xml") is True


def test_move_object_origen_no_existe(s3_bucket, logger: logging.Logger) -> None:
    service = S3Service(_BUCKET, logger)
    with pytest.raises(NotFoundError):
        service.move_object("no_existe.xml", "processed/no_existe.xml")
