"""Tests for session tracking."""

import pytest

from intercache.session import SessionTracker


@pytest.fixture
def tracker(tmp_path):
    return SessionTracker("project_root", cache_dir=tmp_path)


class TestSessionTracker:
    def test_track_and_get(self, tracker):
        tracker.track("s1", "src/main.py")
        tracker.track("s1", "README.md")
        files = tracker.get_session_files("s1")
        assert "src/main.py" in files
        assert "README.md" in files

    def test_separate_sessions(self, tracker):
        tracker.track("s1", "a.py")
        tracker.track("s2", "b.py")
        assert tracker.get_session_files("s1") == ["a.py"]
        assert tracker.get_session_files("s2") == ["b.py"]

    def test_dedup_within_session(self, tracker):
        tracker.track("s1", "a.py")
        tracker.track("s1", "a.py")
        assert tracker.get_session_files("s1") == ["a.py"]

    def test_get_recent_files(self, tracker):
        tracker.track("s1", "old.py")
        tracker.track("s2", "mid.py")
        tracker.track("s3", "new.py")
        files = tracker.get_recent_files(n_sessions=2)
        assert "new.py" in files
        assert "mid.py" in files
        # s1 might or might not be included depending on timestamp ordering
        # since all timestamps are very close

    def test_session_diff(self, tracker):
        tracker.track("s1", "a.py")
        tracker.track("s1", "b.py")
        tracker.track("s2", "b.py")
        tracker.track("s2", "c.py")
        diff = tracker.session_diff("s2", "s1")
        assert diff["only_prev"] == ["a.py"]
        assert diff["only_current"] == ["c.py"]
        assert diff["both"] == ["b.py"]

    def test_recent_session_ids(self, tracker):
        tracker.track("s1", "a.py")
        tracker.track("s2", "b.py")
        tracker.track("s3", "c.py")
        ids = tracker.recent_session_ids(2)
        assert len(ids) <= 2

    def test_empty_tracker(self, tracker):
        assert tracker.get_session_files("nonexistent") == []
        assert tracker.get_recent_files() == []
        assert tracker.recent_session_ids() == []

    def test_prune(self, tracker):
        # Create entries in many sessions
        for i in range(15):
            tracker.track(f"s{i}", f"file_{i}.py")
        removed = tracker.prune(keep_sessions=5)
        assert removed == 10
