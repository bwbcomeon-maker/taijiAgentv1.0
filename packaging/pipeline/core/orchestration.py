"""Platform-neutral stage execution, recovery, and output publication."""

import hashlib
import os
import shutil
import stat
import time
import uuid
from copy import deepcopy
from pathlib import Path

from .errors import PipelineError
from .models import V2_REQUIRED_TOP_LEVEL, new_run_state, utc_now
from .state import RunLock, controller_log, recorded_stage


def _failure_payload(exc):
    return {"category": exc.category, "detail": str(exc), "recorded_at": utc_now()}


def _fetch_staging_path(store, run_id):
    return store.run_dir(run_id) / ".fetch-{}".format(uuid.uuid4().hex[:16])


def _final_output_paths(store, run_id):
    run_dir = store.run_dir(run_id)
    return {"review": run_dir / "review", "remote_log": run_dir / "remote-build.log"}


def _path_is_within(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
    except ValueError:
        return False
    return True


def _safe_metadata(path, kind):
    try:
        metadata = Path(path).lstat()
    except OSError as exc:
        raise PipelineError(
            "{} is missing: {}".format(kind, exc), category="LOCAL_PUBLISH_FAILED"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or (not stat.S_ISDIR(metadata.st_mode) and metadata.st_nlink != 1)
    ):
        raise PipelineError(
            "{} has unsafe metadata".format(kind), category="LOCAL_PUBLISH_FAILED"
        )
    return metadata


def _file_sha256(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise PipelineError(
            "cannot hash published component: {}".format(exc),
            category="LOCAL_PUBLISH_FAILED",
        ) from exc
    return digest.hexdigest()


def _component_identity(path, directory):
    path = Path(path)
    metadata = _safe_metadata(path, "published component")
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise PipelineError(
                "published directory has invalid type", category="LOCAL_PUBLISH_FAILED"
            )
        records = []
        try:
            entries = sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix())
        except OSError as exc:
            raise PipelineError(
                "published directory cannot be listed: {}".format(exc),
                category="LOCAL_PUBLISH_FAILED",
            ) from exc
        for entry in entries:
            relative = entry.relative_to(path).as_posix()
            entry_metadata = _safe_metadata(entry, "published file {}".format(relative))
            if stat.S_ISDIR(entry_metadata.st_mode):
                continue
            if not stat.S_ISREG(entry_metadata.st_mode):
                raise PipelineError(
                    "published directory contains a non-regular file",
                    category="LOCAL_PUBLISH_FAILED",
                )
            records.append(
                {
                    "path": relative,
                    "bytes": entry_metadata.st_size,
                    "sha256": _file_sha256(entry),
                }
            )
        return records
    if not stat.S_ISREG(metadata.st_mode):
        raise PipelineError(
            "published log has invalid type", category="LOCAL_PUBLISH_FAILED"
        )
    return [{"path": path.name, "bytes": metadata.st_size, "sha256": _file_sha256(path)}]


def _remove_staged_component(path):
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(str(path))
    elif path.exists() or path.is_symlink():
        path.unlink()


def _publish_component(staged, final, directory):
    staged = Path(staged)
    final = Path(final)
    staged_identity = _component_identity(staged, directory)
    if final.exists() or final.is_symlink():
        try:
            final_identity = _component_identity(final, directory)
        except PipelineError as exc:
            raise PipelineError(
                "existing output cannot be accepted: {}".format(exc),
                category="LOCAL_OUTPUT_OCCUPIED",
            ) from exc
        if final_identity != staged_identity:
            raise PipelineError(
                "existing output identity differs: {}".format(final),
                category="LOCAL_OUTPUT_OCCUPIED",
            )
        _remove_staged_component(staged)
        return
    try:
        os.rename(str(staged), str(final))
    except OSError as exc:
        raise PipelineError(
            "cannot publish output without overwrite: {}".format(exc),
            category="LOCAL_PUBLISH_FAILED",
        ) from exc


def _publish_fetched_outputs(store, run_id, fetched):
    """Publish the review tree and log with identity-based idempotence."""

    final = _final_output_paths(store, run_id)
    review_requested = Path(os.path.abspath(os.path.expanduser(str(fetched["review_path"]))))
    log_requested = Path(os.path.abspath(os.path.expanduser(str(fetched["remote_log_path"]))))
    review_metadata = _safe_metadata(review_requested, "fetched review")
    log_metadata = _safe_metadata(log_requested, "fetched log")
    if not stat.S_ISDIR(review_metadata.st_mode) or not stat.S_ISREG(log_metadata.st_mode):
        raise PipelineError("fetched output types are invalid", category="LOCAL_PUBLISH_FAILED")
    review_source = review_requested.resolve()
    log_source = log_requested.resolve()
    staging = review_source.parent
    run_dir = store.run_dir(run_id).resolve()
    if (
        log_source.parent != staging
        or not _path_is_within(staging, run_dir)
        or staging == run_dir
    ):
        raise PipelineError(
            "fetched outputs are outside the private run staging directory",
            category="LOCAL_PUBLISH_FAILED",
        )
    _publish_component(review_source, final["review"], True)
    _publish_component(log_source, final["remote_log"], False)
    try:
        staging.rmdir()
    except OSError:
        pass
    return {
        "review_path": str(final["review"]),
        "remote_log_path": str(final["remote_log"]),
    }


def _state_plan(plan):
    result = deepcopy(plan)
    input_state = result.get("input")
    if isinstance(input_state, dict) and input_state.get("status") == "MISSING":
        result["input"] = {"status": "MISSING", "files": {}}
    return result


def _validate_online_plan(plan, finalized, adapter):
    if not isinstance(plan, dict) or not isinstance(finalized, dict):
        raise PipelineError("online plan must be an object", category="PLAN_INVALID")
    keys = tuple(adapter.online_plan_keys)
    if any(key in plan for key in keys):
        raise PipelineError("online plan key already exists", category="PLAN_INVALID")
    added = set(finalized) - set(plan)
    if added != set(keys):
        raise PipelineError("adapter added unexpected online plan fields", category="PLAN_INVALID")
    remainder = deepcopy(finalized)
    for key in keys:
        remainder.pop(key, None)
    if remainder != plan:
        raise PipelineError("adapter changed the frozen candidate plan", category="PLAN_INVALID")
    return deepcopy(finalized)


def _success_patch(adapter, artifact):
    try:
        patch = adapter.success_state_patch(deepcopy(artifact))
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(
            "success state patch failed: {}".format(exc), category="PLAN_INVALID"
        ) from exc
    if not isinstance(patch, dict):
        raise PipelineError("success state patch must be an object", category="PLAN_INVALID")
    if set(patch) & V2_REQUIRED_TOP_LEVEL:
        raise PipelineError(
            "success state patch overlaps standard state fields",
            category="PLAN_INVALID",
        )
    return deepcopy(patch)


def _update_failure(store, run_id, adapter, exc, remote_succeeded):
    current = store.load(run_id)
    remote_succeeded = bool(current.get("remote_build_succeeded")) or remote_succeeded
    pending = remote_succeeded
    store.update(
        run_id,
        {
            "stage": "FETCH_PENDING" if pending else "FAILED",
            "status_label": adapter.pending_label if pending else adapter.not_built_label,
            "finished_at": None if pending else utc_now(),
            "fetch_allowed": pending,
            "failure": _failure_payload(exc),
        },
    )


def execute_build(
    plan,
    online,
    adapter,
    transport,
    store,
    *,
    command_runner,
    input_reader,
    publisher,
):
    run_id = str(plan.get("run_id", ""))
    state_plan = _state_plan(plan)
    state = store.create(run_id, new_run_state(state_plan, online, adapter))
    del state
    controller_log(store, run_id, "run-created")
    remote_succeeded = False
    try:
        with RunLock(store, run_id):
            if plan.get("input", {}).get("status") == "MISSING":
                try:
                    adapter.prepare_input(plan, command_runner)
                except PipelineError:
                    raise
                except Exception as exc:
                    raise PipelineError(
                        "input preparation failed: {}".format(exc),
                        category="INPUT_PREPARATION_FAILED",
                    ) from exc
            try:
                inspected = adapter.inspect_input(
                    Path(plan["repo_root"]), str(plan["source_commit"])
                )
            except PipelineError:
                raise
            except Exception as exc:
                raise PipelineError(
                    "input inspection failed: {}".format(exc),
                    category="INPUT_VERIFICATION_FAILED",
                ) from exc
            if not isinstance(inspected, dict) or inspected.get("status") != "REUSABLE":
                raise PipelineError(
                    "builder input is not reusable after preparation",
                    category="INPUT_VERIFICATION_FAILED",
                )
            files = inspected.get("files")
            manifest = files.get("manifest") if isinstance(files, dict) else None
            manifest_sha256 = manifest.get("sha256") if isinstance(manifest, dict) else None
            bound = store.bind_verified_input(run_id, inspected, manifest_sha256)
            execution_plan = bound["plan"]
            recorded_stage(store, run_id, "INPUT_VERIFIED", lambda: None)

            recorded_stage(
                store, run_id, "REMOTE_RUN_CREATED",
                lambda: transport.create_remote_run(execution_plan),
            )
            recorded_stage(
                store, run_id, "INPUT_TRANSFERRED",
                lambda: transport.transfer_input(execution_plan),
            )
            recorded_stage(
                store, run_id, "REMOTE_INPUT_VERIFIED",
                lambda: transport.verify_remote_input(execution_plan),
            )
            recorded_stage(
                store, run_id, "REMOTE_BUILD_SUCCEEDED",
                lambda: transport.build_remote_candidate(execution_plan),
            )
            remote_succeeded = True
            store.update(run_id, {"remote_build_succeeded": True, "fetch_allowed": True})
            fetched = recorded_stage(
                store, run_id, "REVIEW_FETCHED",
                lambda: transport.fetch(execution_plan, _fetch_staging_path(store, run_id)),
            )
            artifact = recorded_stage(
                store, run_id, "LOCAL_REVIEW_VERIFIED",
                lambda: adapter.validate_review(
                    execution_plan,
                    Path(fetched["review_path"]),
                    Path(fetched["remote_log_path"]),
                ),
            )

            def publish_and_patch():
                try:
                    published = publisher(store, run_id, fetched, artifact)
                except PipelineError:
                    raise
                except Exception as exc:
                    raise PipelineError(
                        "candidate output publication failed: {}".format(exc),
                        category="LOCAL_PUBLISH_FAILED",
                    ) from exc
                patch = _success_patch(adapter, published)
                return published, patch

            published, patch = recorded_stage(
                store, run_id, "CANDIDATE_BUILT", publish_and_patch
            )
            changes = {
                "stage": "CANDIDATE_BUILT",
                "status_label": adapter.success_label,
                "finished_at": utc_now(),
                "fetch_allowed": False,
                "failure": None,
                "artifact": deepcopy(published),
            }
            changes.update(patch)
            store.update(run_id, changes)
    except PipelineError as exc:
        _update_failure(store, run_id, adapter, exc, remote_succeeded)
        raise
    return store.load(run_id)


def execute_fetch(
    state,
    plan,
    adapter,
    transport,
    store,
    *,
    publisher,
):
    run_id = str(state.get("run_id", ""))
    legacy = state.get("schema") != "taiji-package-run-state/v2"
    try:
        with RunLock(store, run_id):
            fetched = recorded_stage(
                store, run_id, "REVIEW_FETCHED",
                lambda: transport.fetch(plan, _fetch_staging_path(store, run_id)),
            )
            artifact = recorded_stage(
                store, run_id, "LOCAL_REVIEW_VERIFIED",
                lambda: adapter.validate_review(
                    plan,
                    Path(fetched["review_path"]),
                    Path(fetched["remote_log_path"]),
                ),
            )

            def publish_and_patch():
                try:
                    published = publisher(store, run_id, fetched, artifact)
                except PipelineError:
                    raise
                except Exception as exc:
                    raise PipelineError(
                        "candidate output publication failed: {}".format(exc),
                        category="LOCAL_PUBLISH_FAILED",
                    ) from exc
                return published, _success_patch(adapter, published)

            published, patch = recorded_stage(
                store, run_id, "CANDIDATE_BUILT", publish_and_patch
            )
            changes = {
                "stage": "CANDIDATE_BUILT",
                "status_label": adapter.success_label,
                "finished_at": utc_now(),
                "fetch_allowed": False,
                "failure": None,
            }
            if legacy:
                changes.update(patch)
            else:
                changes["artifact"] = deepcopy(published)
                changes.update(patch)
            store.update(run_id, changes)
    except PipelineError as exc:
        store.update(
            run_id,
            {
                "stage": "FETCH_PENDING",
                "status_label": adapter.pending_label,
                "fetch_allowed": True,
                "failure": _failure_payload(exc),
            },
        )
        raise
    return store.load(run_id)
