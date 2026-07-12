from webapp.api.models import parse_task_create, parse_task_patch


def test_task_create_requires_mapping_project_and_title() -> None:
    assert parse_task_create([]) is None
    assert parse_task_create({}) is None
    assert parse_task_create({"crm_project_id": "crm-1"}) is None


def test_task_create_normalizes_and_bounds_external_fields() -> None:
    payload = parse_task_create(
        {
            "crm_project_id": " crm-1 ",
            "title": "x" * 600,
            "status": "y" * 50,
            "meta": {"source": "test"},
        }
    )

    assert payload is not None
    assert payload["crm_project_id"] == "crm-1"
    assert len(payload["title"]) == 500
    assert len(payload["status"]) == 32
    assert payload["meta"] == {"source": "test"}


def test_task_patch_allows_only_known_mutable_fields() -> None:
    assert parse_task_patch("invalid") is None
    assert parse_task_patch({"crm_project_id": "forbidden"}) is None

    payload = parse_task_patch(
        {"status": " done ", "title": " New title ", "meta": {}, "unknown": "ignored"}
    )
    assert payload == {"status": "done", "title": "New title", "meta": {}}
