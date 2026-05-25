"""
Property-based tests for payload validation.

Feature: github-runners-ecs
Property 4: Payload validation accepts valid and rejects invalid payloads

Validates: Requirements 4.8
"""

import importlib
import sys
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis.strategies import (
    booleans,
    dictionaries,
    fixed_dictionaries,
    integers,
    just,
    lists,
    none,
    one_of,
    text,
)

# 'lambda' is a Python keyword, so we cannot use a normal import.
_lambda_dir = str(Path(__file__).resolve().parents[2] / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

_webhook_handler = importlib.import_module("webhook_handler")
validate_payload = _webhook_handler.validate_payload

# --- Strategies ---

# Non-empty strings for repository full_name
_non_empty_string = text(min_size=1, max_size=50)

# Non-empty list of labels (at least one string element)
_non_empty_labels = lists(text(min_size=1, max_size=30), min_size=1, max_size=10)

# Extra fields that can appear in the payload without affecting validation
_extra_fields = dictionaries(
    keys=text(min_size=1, max_size=10).filter(
        lambda k: k not in ("action", "workflow_job", "repository")
    ),
    values=text(min_size=0, max_size=20),
    min_size=0,
    max_size=3,
)

# Extra fields inside workflow_job (besides "labels")
_extra_wj_fields = dictionaries(
    keys=text(min_size=1, max_size=10).filter(lambda k: k != "labels"),
    values=integers(),
    min_size=0,
    max_size=3,
)

# Extra fields inside repository (besides "full_name")
_extra_repo_fields = dictionaries(
    keys=text(min_size=1, max_size=10).filter(lambda k: k != "full_name"),
    values=booleans(),
    min_size=0,
    max_size=3,
)

# Actions that are NOT "queued"
_wrong_action = text(min_size=1, max_size=20).filter(lambda s: s != "queued")

# Values that are not dicts (for workflow_job / repository type errors)
_non_dict_values = one_of(
    just(42),
    just("string_value"),
    just([1, 2, 3]),
    just(True),
    just(None),
)

# Values that are not lists (for labels type errors)
_non_list_values = one_of(
    just("not a list"),
    just(42),
    just({"key": "value"}),
    just(True),
)

# Values that are not strings (for full_name type errors)
_non_string_values = one_of(
    just(42),
    just([1, 2]),
    just({"key": "value"}),
    just(True),
)


# --- Property Tests ---


@settings(max_examples=100)
@given(
    labels=_non_empty_labels,
    full_name=_non_empty_string,
    extra_fields=_extra_fields,
    extra_wj=_extra_wj_fields,
    extra_repo=_extra_repo_fields,
)
def test_valid_payload_is_accepted(
    labels: list[str],
    full_name: str,
    extra_fields: dict,
    extra_wj: dict,
    extra_repo: dict,
):
    """
    **Validates: Requirements 4.8**

    For any JSON object containing action="queued", workflow_job.labels as a
    non-empty array, and repository.full_name as a non-empty string, the
    payload validator SHALL accept it (return is_valid=True with no errors),
    regardless of any additional fields present.
    """
    workflow_job = {**extra_wj, "labels": labels}
    repository = {**extra_repo, "full_name": full_name}
    payload = {**extra_fields, "action": "queued", "workflow_job": workflow_job, "repository": repository}

    is_valid, errors = validate_payload(payload)
    assert is_valid is True, f"Expected valid payload but got errors: {errors}"
    assert errors == []


@settings(max_examples=100)
@given(
    labels=_non_empty_labels,
    full_name=_non_empty_string,
)
def test_missing_action_is_rejected(labels: list[str], full_name: str):
    """
    **Validates: Requirements 4.8**

    For any payload missing the 'action' field entirely, the validator
    SHALL reject it (return is_valid=False with at least one error).
    """
    payload = {
        "workflow_job": {"labels": labels},
        "repository": {"full_name": full_name},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    wrong_action=_wrong_action,
    labels=_non_empty_labels,
    full_name=_non_empty_string,
)
def test_wrong_action_is_rejected(wrong_action: str, labels: list[str], full_name: str):
    """
    **Validates: Requirements 4.8**

    For any payload where action is not "queued", the validator SHALL
    reject it (return is_valid=False with at least one error).
    """
    payload = {
        "action": wrong_action,
        "workflow_job": {"labels": labels},
        "repository": {"full_name": full_name},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    full_name=_non_empty_string,
)
def test_missing_workflow_job_is_rejected(full_name: str):
    """
    **Validates: Requirements 4.8**

    For any payload missing the 'workflow_job' field entirely, the validator
    SHALL reject it.
    """
    payload = {
        "action": "queued",
        "repository": {"full_name": full_name},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    non_dict=_non_dict_values,
    full_name=_non_empty_string,
)
def test_workflow_job_wrong_type_is_rejected(non_dict, full_name: str):
    """
    **Validates: Requirements 4.8**

    For any payload where workflow_job is not a dict, the validator
    SHALL reject it.
    """
    # None means the field is effectively "missing" via .get(), skip it
    assume(non_dict is not None)

    payload = {
        "action": "queued",
        "workflow_job": non_dict,
        "repository": {"full_name": full_name},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    full_name=_non_empty_string,
)
def test_empty_labels_is_rejected(full_name: str):
    """
    **Validates: Requirements 4.8**

    For any payload where workflow_job.labels is an empty list, the validator
    SHALL reject it.
    """
    payload = {
        "action": "queued",
        "workflow_job": {"labels": []},
        "repository": {"full_name": full_name},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    non_list=_non_list_values,
    full_name=_non_empty_string,
)
def test_labels_wrong_type_is_rejected(non_list, full_name: str):
    """
    **Validates: Requirements 4.8**

    For any payload where workflow_job.labels is not a list, the validator
    SHALL reject it.
    """
    payload = {
        "action": "queued",
        "workflow_job": {"labels": non_list},
        "repository": {"full_name": full_name},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    labels=_non_empty_labels,
)
def test_missing_repository_is_rejected(labels: list[str]):
    """
    **Validates: Requirements 4.8**

    For any payload missing the 'repository' field entirely, the validator
    SHALL reject it.
    """
    payload = {
        "action": "queued",
        "workflow_job": {"labels": labels},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    non_dict=_non_dict_values,
    labels=_non_empty_labels,
)
def test_repository_wrong_type_is_rejected(non_dict, labels: list[str]):
    """
    **Validates: Requirements 4.8**

    For any payload where repository is not a dict, the validator
    SHALL reject it.
    """
    assume(non_dict is not None)

    payload = {
        "action": "queued",
        "workflow_job": {"labels": labels},
        "repository": non_dict,
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    labels=_non_empty_labels,
)
def test_empty_full_name_is_rejected(labels: list[str]):
    """
    **Validates: Requirements 4.8**

    For any payload where repository.full_name is an empty string, the
    validator SHALL reject it.
    """
    payload = {
        "action": "queued",
        "workflow_job": {"labels": labels},
        "repository": {"full_name": ""},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0


@settings(max_examples=100)
@given(
    non_string=_non_string_values,
    labels=_non_empty_labels,
)
def test_full_name_wrong_type_is_rejected(non_string, labels: list[str]):
    """
    **Validates: Requirements 4.8**

    For any payload where repository.full_name is not a string, the
    validator SHALL reject it.
    """
    payload = {
        "action": "queued",
        "workflow_job": {"labels": labels},
        "repository": {"full_name": non_string},
    }

    is_valid, errors = validate_payload(payload)
    assert is_valid is False
    assert len(errors) > 0
