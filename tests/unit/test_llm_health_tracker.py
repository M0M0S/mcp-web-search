"""Unit tests for LLMHealthTracker — pure dataclass with circular buffer health metrics."""

import time

import pytest

from app.core.llm_client import LLMHealthTracker


class TestLLMHealthTrackerHealthScore:
    """Tests for LLMHealthTracker.get_health_score computation."""

    def test_get_health_score_no_events_returns_1_0(self):
        """Verify health score returns 1.0 when no events recorded for model."""
        tracker = LLMHealthTracker()
        assert tracker.get_health_score("unknown-model") == 1.0

    def test_get_health_score_with_events_mixed_success_failure(self):
        """Verify health score reflects ratio of successes in mixed events."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_failure("model-a")
        tracker.record_success("model-a")
        tracker.record_failure("model-a")
        tracker.record_success("model-a")
        assert tracker.get_health_score("model-a") == pytest.approx(3 / 5)

    def test_get_health_score_all_success(self):
        """Verify health score is 1.0 when all events are successes."""
        tracker = LLMHealthTracker()
        for _ in range(10):
            tracker.record_success("model-a")
        assert tracker.get_health_score("model-a") == 1.0

    def test_get_health_score_all_failure(self):
        """Verify health score is 0.0 when all events are failures."""
        tracker = LLMHealthTracker()
        for _ in range(10):
            tracker.record_failure("model-a")
        assert tracker.get_health_score("model-a") == 0.0


class TestLLMHealthTrackerExclusion:
    """Tests for LLMHealthTracker.should_exclude logic."""

    def test_should_exclude_consecutive_failures_exceeds_threshold(self):
        """Verify should_exclude returns True when consecutive failures exceed threshold."""
        tracker = LLMHealthTracker(health_window=10, failure_threshold=0.5)
        # threshold_count = 0.5 * 10 = 5; need > 5 consecutive failures
        for _ in range(6):
            tracker.record_failure("model-a")
        assert tracker.should_exclude("model-a")

    def test_should_exclude_no_failures(self):
        """Verify should_exclude returns False when no failures recorded."""
        tracker = LLMHealthTracker()
        assert not tracker.should_exclude("model-a")

    def test_should_exclude_at_threshold_boundary_returns_false(self):
        """Verify should_exclude returns False when consecutive failures equal threshold."""
        tracker = LLMHealthTracker(health_window=10, failure_threshold=0.5)
        # 5 consecutive failures = 0.5 * 10 = 5 (not exceeded)
        for _ in range(5):
            tracker.record_failure("model-a")
        assert not tracker.should_exclude("model-a")

    def test_should_exclude_resets_on_interleaved_success(self):
        """Verify consecutive failure count resets after a success event."""
        tracker = LLMHealthTracker(health_window=10, failure_threshold=0.5)
        for _ in range(6):
            tracker.record_failure("model-a")
        assert tracker.should_exclude("model-a")

        # One success resets consecutive count
        tracker.record_success("model-a")
        for _ in range(3):
            tracker.record_failure("model-a")
        assert not tracker.should_exclude("model-a")


class TestLLMHealthTrackerRecordEvents:
    """Tests for LLMHealthTracker.record_success and record_failure."""

    def test_record_success_updates_score(self):
        """Verify record_success increases health score when model was unhealthy."""
        tracker = LLMHealthTracker()
        for _ in range(5):
            tracker.record_failure("model-a")
        assert tracker.get_health_score("model-a") == 0.0

        tracker.record_success("model-a")
        # 5 failures + 1 success = 6 events in buffer (maxlen=10)
        assert tracker.get_health_score("model-a") == pytest.approx(1 / 6)

    def test_record_failure_updates_score(self):
        """Verify record_failure decreases health score when model was healthy."""
        tracker = LLMHealthTracker()
        for _ in range(5):
            tracker.record_success("model-a")
        assert tracker.get_health_score("model-a") == 1.0

        tracker.record_failure("model-a")
        assert tracker.get_health_score("model-a") == pytest.approx(5 / 6)

    def test_record_success_increments_success_count(self):
        """Verify record_success increments _success_counts per model."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_success("model-a")
        tracker.record_success("model-b")
        assert tracker._success_counts["model-a"] == 2
        assert tracker._success_counts["model-b"] == 1

    def test_record_failure_increments_failure_count(self):
        """Verify record_failure increments _failure_counts per model."""
        tracker = LLMHealthTracker()
        tracker.record_failure("model-a")
        tracker.record_failure("model-a")
        tracker.record_failure("model-b")
        assert tracker._failure_counts["model-a"] == 2
        assert tracker._failure_counts["model-b"] == 1

    def test_record_success_updates_last_success_time(self):
        """Verify record_success sets _last_success_times to current time."""
        tracker = LLMHealthTracker()
        before = time.time()
        tracker.record_success("model-a")
        after = time.time()
        assert before <= tracker._last_success_times["model-a"] <= after


class TestLLMHealthTrackerReset:
    """Tests for LLMHealthTracker.reset_model."""

    def test_reset_model_clears_all_state(self):
        """Verify reset_model removes all per-model data structures."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_failure("model-a")
        tracker.record_success("model-a")
        tracker.reset_model("model-a")

        assert "model-a" not in tracker._event_buffers
        assert "model-a" not in tracker._success_counts
        assert "model-a" not in tracker._failure_counts
        assert "model-a" not in tracker._last_success_times
        assert tracker.get_health_score("model-a") == 1.0
        assert not tracker.should_exclude("model-a")

    def test_reset_model_does_not_affect_other_models(self):
        """Verify reset_model only affects the specified model, others intact."""
        tracker = LLMHealthTracker()
        for _ in range(3):
            tracker.record_success("model-a")
        tracker.record_success("model-b")
        for _ in range(2):
            tracker.record_failure("model-b")

        tracker.reset_model("model-a")

        assert tracker.get_health_score("model-a") == 1.0
        # model-b: 1 success + 2 failures = 3 events, score = 1/3
        assert tracker.get_health_score("model-b") == pytest.approx(1 / 3)
        assert tracker._success_counts["model-b"] == 1
        assert tracker._failure_counts["model-b"] == 2


class TestLLMHealthTrackerCircularBuffer:
    """Tests for LLMHealthTracker circular buffer (deque maxlen) behavior."""

    def test_health_window_bounded_circular_buffer(self):
        """Verify event buffer respects maxlen=health_window."""
        tracker = LLMHealthTracker(health_window=5)
        for i in range(7):
            if i % 2 == 0:
                tracker.record_success("model-a")
            else:
                tracker.record_failure("model-a")

        assert len(tracker._event_buffers["model-a"]) == 5

    def test_circular_buffer_keeps_most_recent_events(self):
        """Verify circular buffer retains the most recent events, discards oldest."""
        tracker = LLMHealthTracker(health_window=3)
        tracker.record_success("model-a")  # pushed out
        tracker.record_failure("model-a")
        tracker.record_failure("model-a")
        tracker.record_failure("model-a")

        buffer = tracker._event_buffers["model-a"]
        assert list(buffer) == [False, False, False]
        assert tracker.get_health_score("model-a") == 0.0

    def test_circular_buffer_with_success_at_end(self):
        """Verify health score reflects only events within the window."""
        tracker = LLMHealthTracker(health_window=4)
        # 5 events: S, F, F, F, S — last 4 are F, F, F, S
        tracker.record_success("model-a")
        for _ in range(3):
            tracker.record_failure("model-a")
        tracker.record_success("model-a")

        assert len(tracker._event_buffers["model-a"]) == 4
        assert tracker.get_health_score("model-a") == pytest.approx(1 / 4)


class TestLLMHealthTrackerHealthSummary:
    """Tests for LLMHealthTracker.get_health_summary output format."""

    def test_get_health_summary_format_all_fields_present(self):
        """Verify get_health_summary returns dicts with all required fields."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_failure("model-b")
        tracker.record_success("model-b")

        summary = tracker.get_health_summary()

        # Should have both models sorted
        assert len(summary) == 2
        assert summary[0]["model"] == "model-a"
        assert summary[1]["model"] == "model-b"

        # Verify all fields present in each entry
        required_fields = {
            "model",
            "health_score",
            "last_success_time",
            "consecutive_failures",
            "excluded",
            "success_count",
            "failure_count",
        }
        for entry in summary:
            assert required_fields == set(entry.keys())

    def test_get_health_summary_empty_tracker(self):
        """Verify get_health_summary returns empty list for untouched tracker."""
        tracker = LLMHealthTracker()
        summary = tracker.get_health_summary()
        assert summary == []

    def test_get_health_summary_only_failure_counts_model(self):
        """Verify model appears in summary even if only failure count recorded."""
        tracker = LLMHealthTracker()
        tracker.record_failure("model-x")

        summary = tracker.get_health_summary()
        assert len(summary) == 1
        assert summary[0]["model"] == "model-x"
        assert summary[0]["failure_count"] == 1
        assert summary[0]["success_count"] == 0
