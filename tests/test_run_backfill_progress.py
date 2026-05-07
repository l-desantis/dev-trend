"""Unit tests for _BackfillProgress structlog processor.

Exercises the processor in isolation — no Live session started, no rendering.
"""
import pytest


@pytest.fixture
def prog():
    from scripts.run_backfill import _BackfillProgress
    return _BackfillProgress()


def test_extraction_start_sets_total(prog):
    prog.processor(None, "info", {"event": "extraction_start", "total_rows": 10})
    assert prog._p.tasks[prog._t_extract].total == 10


def test_extraction_checkpoint_advances(prog):
    prog.processor(None, "info", {"event": "extraction_start", "total_rows": 10})
    prog.processor(None, "info", {"event": "extraction_checkpoint", "processed_so_far": 5, "painpoints_so_far": 2})
    task = prog._p.tasks[prog._t_extract]
    assert task.completed == 5
    assert task.fields["extra"] == "pp=2"


def test_extraction_complete_marks_done(prog):
    prog.processor(None, "info", {"event": "extraction_start", "total_rows": 10})
    prog.processor(None, "info", {"event": "extraction_complete"})
    task = prog._p.tasks[prog._t_extract]
    assert task.completed == task.total


def test_labelling_start_and_progress(prog):
    prog.processor(None, "info", {"event": "labelling_start", "unlabelled_found": 3})
    assert prog._p.tasks[prog._t_label].total == 3
    prog.processor(None, "debug", {"event": "labelling_progress"})
    prog.processor(None, "debug", {"event": "labelling_progress"})
    assert prog._p.tasks[prog._t_label].completed == 2


def test_connector_done_increments_conn_bar(prog):
    prog.processor(None, "info", {"event": "backfill_connector_done", "source_type": "reddit", "items_total": 42})
    task = prog._p.tasks[prog._t_conn]
    assert task.completed == 1
    assert "items=42" in task.fields["extra"]


def test_window_done_creates_subtask_lazily(prog):
    assert prog._t_github is None
    prog.processor(None, "info", {"event": "backfill_window_done", "source_type": "github", "items": 7})
    assert prog._t_github is not None
    task = prog._p.tasks[prog._t_github]
    assert task.completed == 1
    assert "items=7" in task.fields["extra"]


def test_unknown_event_passthrough(prog):
    event_dict = {"event": "some_other_event", "foo": "bar"}
    result = prog.processor(None, "info", event_dict)
    assert result is event_dict


def test_identity_resolution_complete(prog):
    prog.processor(None, "info", {"event": "identity_resolution_complete", "attached": 12})
    task = prog._p.tasks[prog._t_identity]
    assert task.completed == 1
    assert "attached=12" in task.fields["extra"]


def test_clustering_complete(prog):
    prog.processor(None, "info", {"event": "clustering_complete", "candidates_created": 5})
    task = prog._p.tasks[prog._t_cluster]
    assert task.completed == 1
    assert "cands=5" in task.fields["extra"]


def test_embedding_complete(prog):
    prog.processor(None, "info", {"event": "embedding_complete", "processed": 100})
    task = prog._p.tasks[prog._t_embed]
    assert task.completed == 100
    assert task.total == 100
