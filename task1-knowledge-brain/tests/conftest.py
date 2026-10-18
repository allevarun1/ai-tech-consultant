import pytest


@pytest.fixture(autouse=True)
def _isolate_repo_files(tmp_path, monkeypatch):
    """Tests must never write to files that are part of the repository."""
    monkeypatch.setenv("KB_ESCALATION_LOG", str(tmp_path / "escalated_questions.jsonl"))
