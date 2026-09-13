"""Tests for analyst skill_id resolution in AutonomousReportGenerator.create_analyst."""
from domains.due_diligence.graph import AutonomousReportGenerator


class TestDedupeAnalystSkillIds:
    def test_no_duplicates_passes_through(self):
        result = AutonomousReportGenerator._dedupe_analyst_skill_ids(
            ["market-analyst", "risk-analyst", "tech-analyst"]
        )
        assert result == ["market-analyst", "risk-analyst", "tech-analyst"]

    def test_duplicate_clears_later_occurrence(self):
        """Reproduces the real gap: prompt forbids reusing a skill_id across
        analysts, but nothing enforced it — the model could assign the same
        card to two analysts and _resolve_analyst_skill_id (existence-only
        check) would let both through unchanged."""
        result = AutonomousReportGenerator._dedupe_analyst_skill_ids(
            ["market-analyst", "market-analyst", "risk-analyst"]
        )
        assert result == ["market-analyst", "", "risk-analyst"]

    def test_multiple_duplicates_of_same_id(self):
        result = AutonomousReportGenerator._dedupe_analyst_skill_ids(
            ["tech-analyst", "tech-analyst", "tech-analyst"]
        )
        assert result == ["tech-analyst", "", ""]

    def test_empty_skill_ids_never_treated_as_duplicates(self):
        result = AutonomousReportGenerator._dedupe_analyst_skill_ids(["", "", ""])
        assert result == ["", "", ""]
