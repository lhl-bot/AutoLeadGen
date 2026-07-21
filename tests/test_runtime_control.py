from pathlib import Path

import pytest

from scripts.set_runtime_control import update_control


def test_runtime_control_release_is_atomic_and_requires_release_identity(tmp_path):
    control = tmp_path / "outbound_hard_pause"
    control.write_text("true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="approved release SHA"):
        update_control(
            directory=tmp_path,
            control="outbound_hard_pause",
            value=False,
            change_id="CHG-1234",
            approved_release_sha=None,
        )

    result = update_control(
        directory=tmp_path,
        control="outbound_hard_pause",
        value=False,
        change_id="CHG-1234",
        approved_release_sha="a" * 40,
    )
    assert control.read_text(encoding="utf-8") == "false\n"
    assert result["previous"] == "true"
    assert result["current"] == "false"
    assert not list(Path(tmp_path).glob(".outbound_hard_pause.*"))


def test_runtime_control_rejects_symlink_target(tmp_path):
    real = tmp_path / "real"
    real.write_text("true\n", encoding="utf-8")
    (tmp_path / "webhook_reject_all").symlink_to(real)

    with pytest.raises(ValueError, match="regular file"):
        update_control(
            directory=tmp_path,
            control="webhook_reject_all",
            value=True,
            change_id="CHG-1234",
            approved_release_sha=None,
        )
