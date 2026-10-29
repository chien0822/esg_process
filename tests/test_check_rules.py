"""檢查規則分級表（SPEC 17.8）。"""

import pytest
from pydantic import ValidationError

from esgfin.constants import (
    ALL_CHECK_RULES,
    P1_CHECK_RULES,
    P2_CHECK_RULES,
    RULE_CITATION_FAIL,
    RULE_INSUFFICIENT_WITHOUT_GAP,
    RULE_JUDGEMENT_TABLE_MISMATCH,
    RULE_SUBCATEGORY_MISMATCH,
    SEVERITIES,
    SEVERITY_MINOR,
    SEVERITY_SEVERE,
)
from esgfin.schemas.check import CheckIssue, CheckReport, P1Check
from esgfin.schemas.final import Ratio


def issue(rule, severity, code="2379-2024-M1-001"):
    return CheckIssue(code=code, rule=rule, severity=severity, message="x")


def test_tables_use_known_severities():
    for table in (P1_CHECK_RULES, P2_CHECK_RULES):
        assert set(table.values()) <= set(SEVERITIES)
    assert set(ALL_CHECK_RULES) == set(P1_CHECK_RULES) | set(P2_CHECK_RULES)


def test_user_decided_grading():
    assert P1_CHECK_RULES[RULE_SUBCATEGORY_MISMATCH] == SEVERITY_SEVERE
    assert RULE_SUBCATEGORY_MISMATCH not in P2_CHECK_RULES  # 移到 S4
    assert P2_CHECK_RULES[RULE_JUDGEMENT_TABLE_MISMATCH] == SEVERITY_SEVERE
    assert P2_CHECK_RULES[RULE_CITATION_FAIL] == SEVERITY_MINOR
    assert P2_CHECK_RULES[RULE_INSUFFICIENT_WITHOUT_GAP] == SEVERITY_MINOR


def test_severity_must_match_table():
    with pytest.raises(ValidationError, match="嚴重度應為"):
        CheckReport(report_id="r", issues=[issue(RULE_CITATION_FAIL, SEVERITY_SEVERE)], citations=[])


def test_rule_must_belong_to_stage():
    with pytest.raises(ValidationError, match="不屬於本階段"):
        CheckReport(report_id="r", issues=[issue(RULE_SUBCATEGORY_MISMATCH, SEVERITY_SEVERE)], citations=[])
    with pytest.raises(ValidationError, match="不屬於本階段"):
        P1Check(report_id="r", items=[], issues=[issue(RULE_JUDGEMENT_TABLE_MISMATCH, SEVERITY_SEVERE)], citations=[])


def test_severe_codes_and_json_roundtrip():
    rep = CheckReport(
        report_id="r",
        issues=[
            issue(RULE_JUDGEMENT_TABLE_MISMATCH, SEVERITY_SEVERE, "A"),
            issue(RULE_CITATION_FAIL, SEVERITY_MINOR, "B"),
        ],
        citations=[],
    )
    assert rep.severe_codes() == {"A"}
    data = rep.model_dump(mode="json")
    assert data["問題"][0]["嚴重度"] == "嚴重"
    assert CheckReport.model_validate(data) == rep


def test_pooled_by_key():
    a = {"x": Ratio(numerator=1, denominator=4)}
    b = {"x": Ratio(numerator=1, denominator=6), "y": Ratio(numerator=0, denominator=6)}
    out = Ratio.pooled_by_key([a, b])
    assert out["x"].value == pytest.approx(2 / 10)
    assert out["y"].denominator == 6
