import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import shutil
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "packaging" / "linux" / "upgrade-data-contract.json"
POSTINST = ROOT / "packaging" / "linux" / "deb" / "postinst"
PRERM = ROOT / "packaging" / "linux" / "deb" / "prerm"
POSTRM = ROOT / "packaging" / "linux" / "deb" / "postrm"
SILENT = ROOT / "packaging" / "linux" / "deb" / "taiji-silent-deploy.sh"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LinuxUpgradeTransactionTest(unittest.TestCase):
    def _module(self):
        from packaging.linux import upgrade_transaction

        return upgrade_transaction

    def _account(self, root: Path):
        module = self._module()
        home = root / "home" / "operator"
        home.mkdir(parents=True)
        return module.AccountIdentity(
            username="operator",
            uid=os.getuid(),
            gid=os.getgid(),
            home=home,
            verified=True,
        )

    def _write_tree(self, account, values=None):
        values = values or {
            "config": "config-value",
            "license": "license-value",
            "sessions": "session-value",
            "attachments": "attachment-value",
            "workspace": "workspace-value",
            "skills": "skill-value",
            "templates": "template-value",
        }
        roots = {
            "config": account.config_dir,
            "data": account.data_dir,
            "state": account.state_dir,
        }
        for root in roots.values():
            root.mkdir(parents=True, exist_ok=True)
        (account.config_dir / "settings.json").write_text(values["config"], encoding="utf-8")
        (account.config_dir / "licenses").mkdir(exist_ok=True)
        (account.config_dir / "licenses" / "active-license.jwt").write_text(
            values["license"], encoding="utf-8"
        )
        for relative, key in (
            ("sessions/current.json", "sessions"),
            ("attachments/item.bin", "attachments"),
            ("workspace/project.txt", "workspace"),
            ("skills/user-skill.md", "skills"),
            ("docx-engine-v2/installed/customer/template.docx", "templates"),
        ):
            path = account.data_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(values[key], encoding="utf-8")
        (account.state_dir / "license-state.json").write_text(
            "state-value", encoding="utf-8"
        )

    def test_snapshot_covers_config_license_sessions_attachments_workspace_skills_and_templates(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            transaction = module.UpgradeTransaction.create(
                root / "state", account=account, operation="upgrade", transaction_id="tx-snapshot"
            )
            manifest = transaction.snapshot_user_data()
            self.assertEqual(manifest["schema"], "taiji-linux-upgrade-snapshot/v1")
            self.assertEqual(
                set(manifest["categories"]),
                {"config", "license", "sessions", "attachments", "workspace", "skills", "templates"},
            )
            self.assertTrue((transaction.backup_dir / "manifest.json").is_file())
            paths = {item["relative"] for item in manifest["files"]}
            self.assertIn(".config/taiji-agent/licenses/active-license.jwt", paths)
            self.assertIn(".local/share/taiji-agent/docx-engine-v2/installed/customer/template.docx", paths)
            self.assertNotIn(str(account.home), json.dumps(manifest))

    def test_sqlite_wal_database_uses_backup_api_and_restores_same_logical_rows(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            restored = root / "restored.db"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO records(value) VALUES ('before')")
                connection.commit()
                with mock.patch.object(module, "_sqlite_backup_call", wraps=module._sqlite_backup_call) as backup:
                    module.sqlite_backup(source, restored)
                    self.assertTrue(backup.called)
            with closing(sqlite3.connect(restored)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM records").fetchall(), [("before",)])

    def test_upgrade_state_transitions_are_fsynced_and_restart_resumable(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            transaction = module.UpgradeTransaction.create(
                root / "state", account=account, operation="upgrade", transaction_id="tx-state"
            )
            candidate = root / "candidate.deb"
            previous = root / "previous.deb"
            signature = root / "previous.deb.sig"
            candidate.write_bytes(b"candidate")
            previous.write_bytes(b"previous")
            signature.write_bytes(b"signature")
            transaction.bind_package_artifacts(
                candidate_deb=candidate,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
            )
            transaction.transition("trusted_staging")
            transaction.transition("stopped")
            resumed = module.UpgradeTransaction.resume(transaction.journal_path, account=account)
            self.assertEqual(resumed.state, "stopped")
            journal_stat = transaction.journal_path.stat()
            self.assertEqual(stat.S_IMODE(journal_stat.st_mode), 0o600)
            self.assertTrue(transaction.journal_path.parent.is_dir())
            with self.assertRaises(module.InvalidTransition):
                resumed.transition("committed")

    def test_upgrade_resume_requires_complete_private_artifact_bundle(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            transaction = module.UpgradeTransaction.create(
                root / "state", account=account, operation="upgrade", transaction_id="tx-artifacts"
            )
            candidate = root / "candidate.deb"
            previous = root / "previous.deb"
            signature = root / "previous.deb.sig"
            candidate.write_bytes(b"candidate")
            previous.write_bytes(b"previous")
            signature.write_bytes(b"signature")
            transaction.bind_package_artifacts(
                candidate_deb=candidate,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
            )
            journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
            journal["candidate_package"] = None
            transaction.journal_path.write_text(json.dumps(journal), encoding="utf-8")
            with self.assertRaises(module.UpgradeError):
                module.UpgradeTransaction.resume_for_account(transaction.journal_path, account)

            journal["candidate_package"] = json.loads(
                transaction.journal_path.read_text(encoding="utf-8")
            ).get("candidate_package")
            original = json.loads(
                (transaction.transaction_dir / "journal.json").read_text(encoding="utf-8")
            )
            original["candidate_package"] = {
                "relative": "artifacts/candidate.deb",
                "basename": candidate.name,
                "sha256": _sha(candidate),
            }
            transaction.journal_path.write_text(json.dumps(original), encoding="utf-8")
            artifacts = transaction.transaction_dir / "artifacts"
            moved = transaction.transaction_dir / "artifacts-real"
            artifacts.rename(moved)
            artifacts.symlink_to(moved, target_is_directory=True)
            with self.assertRaises(module.UnsafeDataError):
                module.UpgradeTransaction.resume_for_account(transaction.journal_path, account)

    def test_missing_previous_deb_or_irreversible_migration_blocks_before_stop(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            transaction = module.UpgradeTransaction.create(root / "state", account=account, transaction_id="tx-block")
            stop = mock.Mock()
            result = transaction.run_upgrade(
                candidate_deb=root / "candidate.deb",
                previous_deb=root / "missing-previous.deb",
                stop_fn=stop,
            )
            self.assertEqual(result["result"], "blocked")
            self.assertEqual(result["state"], "preflight")
            stop.assert_not_called()

    def test_postinst_failure_reinstalls_previous_deb_and_restores_all_hashes(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            transaction = module.UpgradeTransaction.create(root / "state", account=account, transaction_id="tx-fail")
            previous = root / "previous.deb"
            signature = root / "previous.deb.sig"
            candidate = root / "candidate.deb"
            previous.write_bytes(b"previous-package")
            signature.write_bytes(b"previous-signature")
            candidate.write_bytes(b"candidate-package")
            original = {path: _sha(path) for path in account.home.rglob("*") if path.is_file()}
            created_by_migration = account.data_dir / "workspace" / "migration-created.tmp"
            result = transaction.run_upgrade(
                candidate_deb=candidate,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
                stop_fn=lambda: None,
                install_fn=lambda path: path.name == "candidate.deb",
                migrate_fn=lambda: (
                    created_by_migration.write_text("must-not-survive", encoding="utf-8"),
                    (_ for _ in ()).throw(RuntimeError("migration failed")),
                )[-1],
                rollback_install_fn=lambda path: path.name == "previous.deb",
            )
            self.assertEqual(result["result"], "rolled_back")
            self.assertEqual(result["state"], "rolled_back")
            self.assertEqual(
                {path: _sha(path) for path in account.home.rglob("*") if path.is_file()}, original
            )
            self.assertFalse(created_by_migration.exists())

    def test_failed_rollback_reports_manual_recovery_required(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            transaction = module.UpgradeTransaction.create(root / "state", account=account, transaction_id="tx-manual")
            previous = root / "previous.deb"
            signature = root / "previous.deb.sig"
            candidate = root / "candidate.deb"
            previous.write_bytes(b"previous-package")
            signature.write_bytes(b"previous-signature")
            candidate.write_bytes(b"candidate-package")
            result = transaction.run_upgrade(
                candidate_deb=candidate,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
                stop_fn=lambda: None,
                install_fn=lambda path: True,
                migrate_fn=lambda: (_ for _ in ()).throw(RuntimeError("migration failed")),
                rollback_install_fn=lambda path: (_ for _ in ()).throw(RuntimeError("dpkg rollback failed")),
            )
            self.assertEqual(result["result"], "manual_recovery_required")
            self.assertEqual(result["state"], "manual_recovery_required")

    def test_missing_rollback_callback_never_claims_package_was_restored(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            transaction = module.UpgradeTransaction.create(
                root / "state", account=account, transaction_id="tx-no-rollback-callback"
            )
            previous = root / "previous.deb"
            signature = root / "previous.deb.sig"
            candidate = root / "candidate.deb"
            previous.write_bytes(b"previous-package")
            signature.write_bytes(b"previous-signature")
            candidate.write_bytes(b"candidate-package")
            package_marker = root / "package-mutated"

            def partially_install(_path):
                package_marker.write_text("new", encoding="utf-8")
                return False

            result = transaction.run_upgrade(
                candidate_deb=candidate,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
                stop_fn=lambda: None,
                install_fn=partially_install,
            )
            self.assertEqual(result["result"], "manual_recovery_required")
            self.assertEqual(package_marker.read_text(encoding="utf-8"), "new")
            journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
            self.assertEqual(journal["state"], "manual_recovery_required")

    def test_rollback_missing_inputs_persists_manual_recovery_state(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            transaction = module.UpgradeTransaction.create(
                root / "state", account=account, transaction_id="tx-rollback-inputs"
            )
            previous = root / "previous.deb"
            signature = root / "previous.deb.sig"
            candidate = root / "candidate.deb"
            previous.write_bytes(b"previous-package")
            signature.write_bytes(b"previous-signature")
            candidate.write_bytes(b"candidate-package")
            transaction.run_upgrade(
                candidate_deb=candidate,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
                stop_fn=lambda: None,
                install_fn=lambda path: True,
                verify_fn=lambda: True,
                rollback_install_fn=lambda path: True,
            )
            result = transaction.rollback()
            self.assertEqual(result["result"], "manual_recovery_required")
            journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
            self.assertEqual(journal["state"], "manual_recovery_required")

    def test_symlink_mountpoint_wrong_owner_and_unknown_account_fail_closed(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            unsafe = account.data_dir / "workspace" / "unsafe"
            unsafe.symlink_to(account.config_dir / "settings.json")
            transaction = module.UpgradeTransaction.create(root / "state", account=account, transaction_id="tx-safe")
            with self.assertRaises(module.UnsafeDataError):
                transaction.snapshot_user_data()
            shutil.rmtree(account.state_dir)
            account.state_dir.symlink_to(root / "missing-state-root")
            with self.assertRaises(module.UnsafeDataError):
                transaction.snapshot_user_data()
            redirected_home = self._account(root / "redirected")
            redirected_home.config_dir.parent.mkdir(parents=True, exist_ok=True)
            external_config = root / "external-config"
            (external_config / "taiji-agent").mkdir(parents=True)
            redirected_home.config_dir.parent.rmdir()
            redirected_home.config_dir.parent.symlink_to(external_config, target_is_directory=True)
            redirected_transaction = module.UpgradeTransaction.create(
                root / "redirected-state", account=redirected_home, transaction_id="tx-parent-link"
            )
            with self.assertRaises(module.UnsafeDataError):
                redirected_transaction.snapshot_user_data()
            with self.assertRaises(module.UnsafeDataError):
                module.validate_account(module.AccountIdentity("nobody", 999999, 999999, root / "missing", verified=False))
            with mock.patch.object(module.os.path, "ismount", return_value=True):
                with self.assertRaises(module.UnsafeDataError):
                    module.ensure_not_mountpoint(root)

    def test_same_version_reinstall_is_idempotent_without_user_data_replacement(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            marker = account.data_dir / "workspace" / "project.txt"
            marker.write_text("customer-edited", encoding="utf-8")
            transaction = module.UpgradeTransaction.create(
                root / "state", account=account, operation="reinstall", transaction_id="tx-reinstall"
            )
            result = transaction.run_reinstall(version="1.0.0", current_version="1.0.0")
            self.assertEqual(result["result"], "reinstalled")
            self.assertEqual(marker.read_text(encoding="utf-8"), "customer-edited")

    def test_successful_upgrade_then_rollback_then_upgrade_again_preserves_data(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = self._account(root)
            self._write_tree(account)
            marker = account.data_dir / "workspace" / "project.txt"
            previous = root / "previous.deb"
            signature = root / "previous.deb.sig"
            candidate = root / "candidate.deb"
            newer = root / "newer.deb"
            previous.write_bytes(b"previous-package")
            signature.write_bytes(b"previous-signature")
            candidate.write_bytes(b"candidate-package")
            newer.write_bytes(b"newer-package")
            first = module.UpgradeTransaction.create(root / "state", account=account, transaction_id="tx-upgrade-1")
            first_result = first.run_upgrade(
                candidate_deb=candidate,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
                stop_fn=lambda: None,
                install_fn=lambda path: True,
                migrate_fn=lambda: marker.write_text("v2", encoding="utf-8"),
                verify_fn=lambda: True,
                rollback_install_fn=lambda path: True,
            )
            self.assertEqual(first_result["result"], "upgraded")
            rollback = first.rollback(
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
                rollback_install_fn=lambda path: path.name == "previous.deb",
            )
            self.assertEqual(rollback["result"], "rolled_back")
            second = module.UpgradeTransaction.create(root / "state", account=account, transaction_id="tx-upgrade-2")
            second_result = second.run_upgrade(
                candidate_deb=newer,
                previous_deb=previous,
                previous_sha256=_sha(previous),
                previous_signature=signature,
                stop_fn=lambda: None,
                install_fn=lambda path: True,
                migrate_fn=lambda: marker.write_text("v3", encoding="utf-8"),
                verify_fn=lambda: True,
                rollback_install_fn=lambda path: True,
            )
            self.assertEqual(second_result["result"], "upgraded")
            self.assertEqual(marker.read_text(encoding="utf-8"), "v3")

    def test_postinst_never_invokes_apt_or_writes_user_home(self):
        for script in (POSTINST, PRERM, POSTRM):
            source = script.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"\bapt(?:-get)?\b")
            self.assertNotIn("$HOME", source)
            self.assertNotIn("${HOME", source)

    def test_silent_upgrade_stages_previous_deb_before_any_rollback_dpkg(self):
        source = SILENT.read_text(encoding="utf-8")
        self.assertIn("stage_previous_for_rollback", source)
        self.assertIn("STAGED_PREVIOUS_DEB_PATH", source)
        main = source.split("main() {", 1)[1]
        self.assertLess(main.index("stage_candidate_for_install"), main.index("stage_previous_for_rollback"))
        self.assertLess(main.index("stage_previous_for_rollback"), main.index("prepare_upgrade_transaction"))
        self.assertLess(main.index("prepare_upgrade_transaction"), main.index("stop_managed_runtime_before_snapshot"))
        self.assertLess(main.index("stop_managed_runtime_before_snapshot"), main.index("snapshot_upgrade_transaction"))
        rollback_body = source.split("rollback_previous_package() {", 1)[1].split("}\n\n", 1)[0]
        self.assertIn("STAGED_PREVIOUS_DEB_PATH", rollback_body)
        self.assertIn("verify_rollback_package", source)
        self.assertIn("dpkg-query -W -f='${db:Status-Status}' taiji-agent", source)
        self.assertIn('/proc/$pid/exe', source)
        self.assertIn('ps -eo pid=', source)
        self.assertNotIn('ps -eo pid=,args=', source)


if __name__ == "__main__":
    unittest.main()
