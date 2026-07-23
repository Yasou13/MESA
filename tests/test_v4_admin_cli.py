"""V4 operator CLI key and authorization bootstrap contracts."""

from __future__ import annotations

from mesa_memory.security.admin_cli import main


def test_admin_cli_issues_hash_backed_key_and_grants_scope(tmp_path, capsys) -> None:
    policy = tmp_path / "rbac.sqlite"
    assert (
        main(
            [
                "--policy-db",
                str(policy),
                "issue-key",
                "--principal",
                "principal-a",
                "--key-id",
                "operator-a",
            ]
        )
        == 0
    )
    credential = capsys.readouterr().out.strip()
    assert credential.startswith("operator-a.")
    assert credential not in policy.read_bytes().decode("utf-8", errors="ignore")

    assert (
        main(
            [
                "--policy-db",
                str(policy),
                "grant-role",
                "--principal",
                "principal-a",
                "--tenant",
                "tenant-a",
                "--workspace",
                "workspace-a",
                "--dataset",
                "dataset-a",
                "--role",
                "OWNER",
            ]
        )
        == 0
    )
    assert "role-granted" in capsys.readouterr().out


def test_admin_cli_rejects_dataset_role_without_workspace(tmp_path, capsys) -> None:
    result = main(
        [
            "--policy-db",
            str(tmp_path / "rbac.sqlite"),
            "grant-role",
            "--principal",
            "principal-a",
            "--tenant",
            "tenant-a",
            "--dataset",
            "dataset-a",
            "--role",
            "WRITER",
        ]
    )
    assert result == 2
    assert "--dataset requires --workspace" in capsys.readouterr().err
