"""Memoria de la sesión: validación y fusión del delta, sin LLM."""

from __future__ import annotations

from app.schemas.session import MetadataUpdate, ProjectMetadata
from app.state.memory import apply_update, validate_update


def test_union_ignores_case_and_keeps_first_spelling() -> None:
    memory = ProjectMetadata(mentioned_technologies=["React"])
    merged = apply_update(memory, MetadataUpdate(add_technologies=["react", "PostgreSQL"]), turn=2)
    assert merged.metadata.mentioned_technologies == ["React", "PostgreSQL"]


def test_removing_a_technology_rejects_it_and_logs_the_change() -> None:
    memory = ProjectMetadata(mentioned_technologies=["Rails", "React"])
    update = MetadataUpdate(
        remove_technologies=["rails"],
        add_technologies=["Node.js"],
        evidence="ya no usamos Rails, vamos con Node",
    )
    merged = apply_update(memory, update, turn=3)
    assert merged.metadata.mentioned_technologies == ["React", "Node.js"]
    assert merged.metadata.rejected_options == ["rails"]
    fields = {change.field for change in merged.changes}
    assert fields == {"mentioned_technologies", "rejected_options"}
    assert all(change.turn == 3 and change.evidence for change in merged.changes)


def test_adding_a_rejected_option_back_revives_it() -> None:
    memory = ProjectMetadata(rejected_options=["Microservicios"])
    merged = apply_update(memory, MetadataUpdate(add_technologies=["microservicios"]), turn=4)
    assert merged.metadata.rejected_options == []
    assert merged.metadata.mentioned_technologies == ["microservicios"]


def test_rejected_option_cannot_stay_among_technologies() -> None:
    memory = ProjectMetadata(mentioned_technologies=["Kafka"])
    merged = apply_update(memory, MetadataUpdate(add_rejected_options=["kafka"]), turn=2)
    assert merged.metadata.mentioned_technologies == []
    assert merged.metadata.rejected_options == ["kafka"]


def test_empty_update_keeps_every_fact() -> None:
    memory = ProjectMetadata(project_name="Atlas", assumed_team_size=3, agreed_scope="MVP")
    merged = apply_update(memory, MetadataUpdate(), turn=5)
    assert merged.metadata == memory
    assert merged.changes == []


def test_validation_discards_out_of_range_and_contradictions() -> None:
    update = MetadataUpdate(
        set_team_size=900,
        set_project_name="x" * 500,
        add_technologies=["Go", "Rust"],
        remove_technologies=["go"],
    )
    cleaned, discarded = validate_update(update)
    assert cleaned.set_team_size is None
    assert cleaned.set_project_name is None
    assert cleaned.add_technologies == ["Rust"]
    assert cleaned.remove_technologies == []
    assert len(discarded) == 3


def test_scalar_is_replaced_only_by_a_valid_value() -> None:
    memory = ProjectMetadata(assumed_team_size=3)
    merged = apply_update(memory, MetadataUpdate(set_team_size=0), turn=2)
    assert merged.metadata.assumed_team_size == 3
    merged = apply_update(memory, MetadataUpdate(set_team_size=5), turn=2)
    assert merged.metadata.assumed_team_size == 5


def test_full_list_keeps_established_facts_and_reports_overflow() -> None:
    from app.schemas.session import MAX_LIST_ITEMS

    existing = [f"tech-{index}" for index in range(MAX_LIST_ITEMS)]
    memory = ProjectMetadata(mentioned_technologies=existing)
    merged = apply_update(memory, MetadataUpdate(add_technologies=["Nueva"]), turn=9)
    assert merged.metadata.mentioned_technologies == existing
    assert any("lista llena" in reason for reason in merged.discarded)


def test_full_rejected_list_still_retires_the_technology() -> None:
    from app.schemas.session import MAX_LIST_ITEMS

    rejected = [f"old-{index}" for index in range(MAX_LIST_ITEMS)]
    memory = ProjectMetadata(mentioned_technologies=["Rails", "React"], rejected_options=rejected)
    merged = apply_update(memory, MetadataUpdate(remove_technologies=["Rails"]), turn=4)
    assert merged.metadata.mentioned_technologies == ["React"]
    assert merged.metadata.rejected_options == rejected
    assert any("rejected_options: lista llena" in reason for reason in merged.discarded)


def test_revival_only_when_the_technology_fits() -> None:
    from app.schemas.session import MAX_LIST_ITEMS

    full = [f"tech-{index}" for index in range(MAX_LIST_ITEMS)]
    memory = ProjectMetadata(mentioned_technologies=full, rejected_options=["Kafka"])
    merged = apply_update(memory, MetadataUpdate(add_technologies=["kafka"]), turn=5)
    assert merged.metadata.rejected_options == ["Kafka"]  # No entró: sigue rechazada.
    assert "kafka" not in [item.casefold() for item in merged.metadata.mentioned_technologies]
