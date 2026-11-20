"""Tests unitarios para la codificación/decodificación del campo 'id'."""

from __future__ import annotations

import base64

import pytest

from id_codec import decode_key, encode_key


def test_encode_decode_roundtrip():
    key = "fractar/unprocessed/2026/07/15/xml/factura001.xml"
    token = encode_key(key)

    assert decode_key(token) == key


def test_encode_es_url_safe():
    key = "fractar/unprocessed/2026/07/15/xml/factura 001 (final).xml"
    token = encode_key(key)

    # Base64 URL-safe: no debe contener '+' ni '/'.
    assert "+" not in token
    assert "/" not in token


def test_decode_token_invalido():
    # Bytes que no forman una secuencia UTF-8 válida tras decodificar Base64.
    invalid_utf8_token = base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode("ascii").rstrip("=")

    with pytest.raises(ValueError):
        decode_key(invalid_utf8_token)
