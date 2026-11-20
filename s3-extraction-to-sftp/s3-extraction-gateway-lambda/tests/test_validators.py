"""Tests unitarios para el módulo de validaciones."""

from __future__ import annotations

import pytest

from errors import ValidationError
from validators import validate_action, validate_pending_payload, validate_processed_payload


def test_validate_action_ok():
    assert validate_action({"action": "pending"}) == "pending"
    assert validate_action({"action": "processed"}) == "processed"


def test_validate_action_falta():
    with pytest.raises(ValidationError):
        validate_action({})


def test_validate_action_no_soportada():
    with pytest.raises(ValidationError):
        validate_action({"action": "list"})


def test_validate_pending_payload_ok():
    prefix = validate_pending_payload(
        {"prefix": "fractar/unprocessed/"}, "fractar/unprocessed/"
    )
    assert prefix == "fractar/unprocessed/"


def test_validate_pending_payload_normaliza_barra_final():
    prefix = validate_pending_payload(
        {"prefix": "fractar/unprocessed"}, "fractar/unprocessed/"
    )
    assert prefix == "fractar/unprocessed/"


def test_validate_pending_payload_falta():
    with pytest.raises(ValidationError):
        validate_pending_payload({}, "fractar/unprocessed/")


def test_validate_pending_payload_no_coincide():
    with pytest.raises(ValidationError):
        validate_pending_payload({"prefix": "otro/prefijo/"}, "fractar/unprocessed/")


def test_validate_processed_payload_ok():
    request_id, id_token, processed_prefix = validate_processed_payload(
        {"requestId": "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3", "id": "abc123"}
    )
    assert request_id == "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3"
    assert id_token == "abc123"
    assert processed_prefix is None


def test_validate_processed_payload_con_processed_prefix():
    _, _, processed_prefix = validate_processed_payload(
        {
            "requestId": "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3",
            "id": "abc123",
            "processedPrefix": "otro/destino",
        }
    )
    assert processed_prefix == "otro/destino/"


def test_validate_processed_payload_falta_request_id():
    with pytest.raises(ValidationError):
        validate_processed_payload({"id": "abc123"})


def test_validate_processed_payload_falta_id():
    with pytest.raises(ValidationError):
        validate_processed_payload({"requestId": "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3"})


def test_validate_processed_payload_processed_prefix_vacio():
    with pytest.raises(ValidationError):
        validate_processed_payload(
            {
                "requestId": "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3",
                "id": "abc123",
                "processedPrefix": "   ",
            }
        )
