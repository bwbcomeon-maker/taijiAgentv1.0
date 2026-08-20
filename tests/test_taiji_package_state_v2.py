import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.taiji_package_fixtures import (
    canonical_json_sha256_for_fixture,
    complete_input_files,
    complete_online,
    complete_plan,
    complete_v2_payload,
    complete_v1_fetch_pending,
    write_secure_v1_state,
)


ROOT = Path(__file__).resolve().parents[1]


def required(module_name, symbol):
    try:
        module = importlib.import_module(module_name)
        return getattr(module, symbol)
    except (ImportError, AttributeError) as exc:
        raise AssertionError(
            "missing production symbol {}.{}: {}".format(module_name, symbol, exc)
        )


class FixtureAdapter:
    not_built_label = "候选 DEB 未构建"

    def initial_state_patch(self, plan, online):
        del plan, online
        return {"identity": {}, "policy": None}

    def success_state_patch(self, artifact):
        del artifact
        return {}


def nested_change(path, value):
    pieces = path.split(".")
    result = value
    for piece in reversed(pieces):
        result = {piece: result}
    return result


class TaijiPackageStateV2Tests(unittest.TestCase):
    def test_new_run_state_populates_complete_v2_contract(self):
        new_run_state = required("packaging.pipeline.core.models", "new_run_state")
        required_top_level = required(
            "packaging.pipeline.core.models", "V2_REQUIRED_TOP_LEVEL"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = complete_plan(root)
            state = new_run_state(plan, complete_online(), FixtureAdapter())
        self.assertEqual(state["schema"], "taiji-package-run-state/v2")
        self.assertEqual(set(state), set(required_top_level))
        self.assertEqual(
            state["target_config_sha256"],
            canonical_json_sha256_for_fixture(plan["target_config"]),
        )
        self.assertEqual(state["source"]["commit"], plan["source_commit"])
        self.assertEqual(state["identity"]["controller_commit"], plan["controller_commit"])
        self.assertEqual(state["identity"]["host_facts_sha256"], "d" * 64)
        self.assertEqual(state["stage"], "PLANNED")
        self.assertEqual(state["status_label"], "候选 DEB 未构建")
        self.assertIsNone(state["policy"])

    def test_target_config_sha_uses_validated_canonical_json(self):
        canonical_json_sha256 = required(
            "packaging.pipeline.core.models", "canonical_json_sha256"
        )
        first = {"target_id": "kylin-amd64", "architecture": "amd64"}
        second = {"architecture": "amd64", "target_id": "kylin-amd64"}
        self.assertEqual(canonical_json_sha256(first), canonical_json_sha256(second))
        second["host_alias"] = "kylin"
        self.assertNotEqual(canonical_json_sha256(first), canonical_json_sha256(second))

    def test_create_rejects_every_missing_required_top_level_field(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for field in complete_v2_payload(root):
                with self.subTest(field=field):
                    payload = complete_v2_payload(root)
                    del payload[field]
                    store = store_type(root / ("state-" + field.replace("/", "-")))
                    with self.assertRaises(pipeline_error) as context:
                        store.create("run-1", payload)
                    self.assertEqual(context.exception.category, "PLAN_INVALID")

    def test_create_rejects_invalid_required_nested_field_type(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        cases = {
            "source.commit": None,
            "identity.controller_commit": 3,
            "host.alias": 3,
            "paths.local_run_dir": 3,
            "logs.controller": 3,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path, value in cases.items():
                with self.subTest(path=path):
                    payload = complete_v2_payload(root)
                    pieces = path.split(".")
                    payload[pieces[0]][pieces[1]] = value
                    store = store_type(root / ("state-" + pieces[1]))
                    with self.assertRaises(pipeline_error) as context:
                        store.create("run-1", payload)
                    self.assertEqual(context.exception.category, "PLAN_INVALID")

    def test_update_rejects_each_frozen_identity_path(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        changes = {
            "schema": "other",
            "run_id": "other",
            "created_at": "other",
            "target_id": "other",
            "target_config": {"changed": True},
            "target_config_sha256": "f" * 64,
            "source.repo_root": "/other",
            "source.branch": "other",
            "source.commit": "f" * 40,
            "source.tree": "f" * 40,
            "identity.controller_commit": "f" * 40,
            "host.alias": "other",
            "host.remote_run_dir": "/other",
            "paths.local_run_dir": "/other",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path, value in changes.items():
                with self.subTest(path=path):
                    state_root = root / path.replace(".", "-")
                    store = store_type(state_root)
                    store.create("run-1", complete_v2_payload(root))
                    change = (
                        nested_change(path, value)
                        if "." in path else {path: value}
                    )
                    with self.assertRaises(pipeline_error) as context:
                        store.update("run-1", change)
                    self.assertEqual(context.exception.category, "PLAN_INVALID")

    def test_nullable_identity_can_move_null_to_sha_once(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = store_type(root / "state")
            store.create("run-1", complete_v2_payload(root))
            updated = store.update(
                "run-1", {"identity": {"asset_provenance_sha256": "f" * 64}}
            )
            self.assertEqual(updated["identity"]["asset_provenance_sha256"], "f" * 64)
            for value in ("e" * 64, None):
                with self.subTest(value=value):
                    with self.assertRaises(pipeline_error) as context:
                        store.update(
                            "run-1", {"identity": {"asset_provenance_sha256": value}}
                        )
                    self.assertEqual(context.exception.category, "PLAN_INVALID")

    def test_missing_input_can_bind_once_before_input_verified(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = store_type(root / "state")
            store.create("run-1", complete_v2_payload(root, input_status="MISSING"))
            files = complete_input_files(root)
            bound = store.bind_verified_input(
                "run-1",
                {"status": "REUSABLE", "source_commit": "a" * 40, "files": files},
                files["manifest"]["sha256"],
            )
            self.assertEqual(bound["input"]["files"], files)
            self.assertEqual(bound["plan"]["input"], bound["input"])
            self.assertEqual(
                bound["identity"]["input_manifest_sha256"], files["manifest"]["sha256"]
            )
            changed = copy.deepcopy(files)
            changed["archive"]["bytes"] += 1
            with self.assertRaises(pipeline_error) as context:
                store.bind_verified_input(
                    "run-1",
                    {"status": "REUSABLE", "source_commit": "a" * 40, "files": changed},
                    changed["manifest"]["sha256"],
                )
            self.assertEqual(context.exception.category, "PLAN_INVALID")

    def test_missing_input_binds_top_level_and_execution_plan_atomically(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = store_type(root / "state")
            store.create("run-1", complete_v2_payload(root, input_status="MISSING"))
            state_path = store.state_path("run-1")
            before = state_path.read_bytes()
            store._atomic_write = lambda run_id, state: (_ for _ in ()).throw(
                OSError("injected before replace")
            )
            files = complete_input_files(root)
            with self.assertRaises(Exception):
                store.bind_verified_input(
                    "run-1",
                    {"status": "REUSABLE", "source_commit": "a" * 40, "files": files},
                    files["manifest"]["sha256"],
                )
            self.assertEqual(state_path.read_bytes(), before)
            state = json.loads(before.decode("utf-8"))
            self.assertEqual(state["input"]["status"], "MISSING")
            self.assertEqual(state["plan"]["input"]["status"], "MISSING")
            self.assertIsNone(state["identity"]["input_manifest_sha256"])

    def test_reusable_input_rewrite_requires_identical_identity(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = store_type(root / "state")
            payload = complete_v2_payload(root, input_status="REUSABLE")
            store.create("run-1", payload)
            files = copy.deepcopy(payload["input"]["files"])
            inspected = {"status": "REUSABLE", "files": files}
            store.bind_verified_input(
                "run-1",
                inspected,
                files["manifest"]["sha256"],
            )
            files["manifest"]["sha256"] = "f" * 64
            with self.assertRaises(pipeline_error) as context:
                store.bind_verified_input(
                    "run-1",
                    inspected,
                    files["manifest"]["sha256"],
                )
            self.assertEqual(context.exception.category, "PLAN_INVALID")

    def test_input_is_frozen_after_input_verified(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = store_type(root / "state")
            store.create("run-1", complete_v2_payload(root, input_status="MISSING"))
            files = complete_input_files(root)
            store.bind_verified_input(
                "run-1",
                {"status": "REUSABLE", "source_commit": "a" * 40, "files": files},
                files["manifest"]["sha256"],
            )
            store.update("run-1", {"stage": "INPUT_VERIFIED"})
            state_path = store.state_path("run-1")
            before = state_path.read_bytes()
            files["archive"]["bytes"] += 1
            with self.assertRaises(pipeline_error) as context:
                store.update(
                    "run-1",
                    {"input": {"status": "REUSABLE", "files": files}},
                )
            self.assertEqual(context.exception.category, "PLAN_INVALID")
            self.assertEqual(state_path.read_bytes(), before)

    def test_v1_load_and_update_preserve_schema_and_bytes_until_update(self):
        store_type = required("packaging.pipeline.core.state", "RunStateStore")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / "state"
            payload = complete_v1_fetch_pending(root)
            path = write_secure_v1_state(state_root, "legacy-run", payload)
            before = path.read_bytes()
            store = store_type(state_root)
            loaded = store.load("legacy-run")
            self.assertEqual(path.read_bytes(), before)
            updated = store.update("legacy-run", {"status_label": "仍待恢复"})
            self.assertEqual(updated["schema"], "taiji-package-run-state/v1")
            self.assertNotIn("target_config_sha256", updated)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], "taiji-package-run-state/v1")
            self.assertEqual(loaded["schema"], "taiji-package-run-state/v1")

    def test_core_has_no_v1_linux_field_mapping(self):
        forbidden = ("canonical_policy_sha256", "deb_sha256", "normalize_legacy_state")
        for relative in (
            "packaging/pipeline/core/models.py",
            "packaging/pipeline/core/state.py",
        ):
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            text = path.read_text(encoding="utf-8")
            for literal in forbidden:
                with self.subTest(path=relative, literal=literal):
                    self.assertNotIn(literal, text)


if __name__ == "__main__":
    unittest.main()
