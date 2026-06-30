from tools.audit_post_fix_issues import audit


def test_audit_reports_all_issues_with_valid_schema():
    rows = audit()
    assert [row["issue_id"] for row in rows] == list("ABCDEFGHIJKL")
    assert all(row["evidence"] and "safe_to_fix" in row for row in rows)
