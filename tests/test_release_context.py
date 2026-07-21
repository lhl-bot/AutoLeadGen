from scripts.check_release_context import release_context_findings


def test_release_context_rejects_customer_evidence_and_strong_secrets(tmp_path):
    evidence = tmp_path / "workflow18_example.json"
    private_key = tmp_path / "temporary_release_scan_fixture.txt"
    evidence.write_text("{}", encoding="utf-8")
    private_key.write_text(
        "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key\n",
        encoding="utf-8",
    )
    findings = release_context_findings([evidence, private_key], root=tmp_path)

    assert "forbidden_release_evidence:workflow18_example.json" in findings
    assert (
        "strong_secret_pattern:private_key:temporary_release_scan_fixture.txt"
        in findings
    )


def test_release_context_accepts_normal_source_file(tmp_path):
    fixture = tmp_path / "normal_source.py"
    fixture.write_text("print('safe')\n", encoding="utf-8")
    assert release_context_findings([fixture], root=tmp_path) == []
