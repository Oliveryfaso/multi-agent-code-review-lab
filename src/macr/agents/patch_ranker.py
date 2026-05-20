from __future__ import annotations

from macr.schemas import PatchArtifact


class PatchRankerAgent:
    """Ranks verified patch candidates with conservative engineering heuristics."""

    def rank(self, candidates: list[PatchArtifact]) -> list[dict]:
        scored = []
        for index, candidate in enumerate(candidates):
            verification = {
                key: value
                for key, value in (candidate.verification or {}).items()
                if key != "candidate_ranking"
            }
            score = 0.0
            reasons: list[str] = []
            if candidate.diff.strip():
                score += 10
                reasons.append("has_diff")
            else:
                score -= 20
                reasons.append("empty_diff")
            if verification.get("patch_apply_check") == "passed":
                score += 45
                reasons.append("applies_cleanly")
            elif verification.get("patch_apply_check") == "skipped":
                score -= 10
                reasons.append("apply_skipped")
            else:
                score -= 60
                reasons.append("apply_failed")
            if verification.get("test_check") == "passed":
                score += 35
                reasons.append("tests_passed")
            elif verification.get("test_check") == "failed":
                score -= 50
                reasons.append("tests_failed")
            diff_lines = [line for line in candidate.diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
            if len(diff_lines) <= 8:
                score += 8
                reasons.append("small_diff")
            elif len(diff_lines) > 40:
                score -= 10
                reasons.append("large_diff")
            if len(candidate.target_files) <= 1:
                score += 4
                reasons.append("single_file_scope")
            if candidate.source == "template":
                score += 3
                reasons.append("deterministic_template")
            scored.append(
                {
                    "index": index,
                    "score": round(score, 2),
                    "source": candidate.source,
                    "summary": candidate.summary,
                    "target_files": candidate.target_files,
                    "reasons": reasons,
                    "verification": verification,
                }
            )
        return sorted(scored, key=lambda item: item["score"], reverse=True)

    def choose(self, candidates: list[PatchArtifact]) -> tuple[PatchArtifact, list[dict]]:
        ranking = self.rank(candidates)
        if not ranking:
            raise ValueError("no patch candidates to rank")
        return candidates[ranking[0]["index"]], ranking
