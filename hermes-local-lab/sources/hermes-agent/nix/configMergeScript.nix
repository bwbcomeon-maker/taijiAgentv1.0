# nix/configMergeScript.nix — Canonical NixOS config/.env activation writer
#
# The caller supplies the packaged Hermes Python interpreter.  This keeps the
# activation path on the same canonical credential transaction implementation
# as the CLI and WebUI instead of using a second direct file writer.
{ pkgs, python }:
pkgs.writeScript "hermes-config-merge" ''
  #!${python}
  from __future__ import annotations

  import argparse
  import copy
  import json
  import os
  from collections.abc import Mapping
  from pathlib import Path
  from typing import Any

  from agent.image_gen_verification import (
      CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
      CAPABILITY_CONFIG_EPOCH_VISION,
      bump_capability_config_epochs,
      capability_config_epoch,
      capability_epochs_for_secret_env,
      reconcile_capability_config_epochs,
  )
  from agent.provider_credentials import (
      _encode_env_value,
      _parse_config_bytes,
      _parse_env_bytes,
      credential_transaction,
      load_credential_snapshot,
      mutate_config_strict,
      replace_config_env_payload_strict,
  )


  CAPABILITIES = (
      CAPABILITY_CONFIG_EPOCH_VISION,
      CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
  )


  def parse_args() -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          description=(
              "Publish NixOS-managed Hermes config and optional environment "
              "through the canonical credential transaction."
          )
      )
      parser.add_argument(
          "--replace",
          action="store_true",
          help="replace user config instead of recursively merging it",
      )
      parser.add_argument(
          "--env-json",
          type=Path,
          help="JSON mapping of declarative non-secret environment values",
      )
      parser.add_argument(
          "--env-file",
          action="append",
          default=[],
          type=Path,
          help="declarative env file; later files override earlier values",
      )
      parser.add_argument(
          "--run-as-uid",
          type=int,
          help=(
              "drop permanently to this uid after root-only inputs are "
              "validated and the canonical transaction lock is held"
          ),
      )
      parser.add_argument(
          "--run-as-gid",
          type=int,
          help="primary gid paired with --run-as-uid",
      )
      parser.add_argument("source", type=Path)
      parser.add_argument("config_path", type=Path)
      return parser.parse_args()


  def deep_merge(
      base: Mapping[str, Any],
      override: Mapping[str, Any],
  ) -> dict[str, Any]:
      result = copy.deepcopy(dict(base))
      for key, value in override.items():
          if (
              key in result
              and isinstance(result[key], Mapping)
              and isinstance(value, Mapping)
          ):
              result[key] = deep_merge(result[key], value)
          else:
              result[key] = copy.deepcopy(value)
      return result


  def load_declared_config(path: Path) -> dict[str, Any]:
      # Reuse the strict canonical parser so duplicate mapping keys, invalid
      # UTF-8, and non-mapping roots fail before any live state is touched.
      return _parse_config_bytes(path.read_bytes())


  def reject_duplicate_json_keys(
      pairs: list[tuple[str, Any]],
  ) -> dict[str, Any]:
      result: dict[str, Any] = {}
      for key, value in pairs:
          if key in result:
              raise ValueError(
                  "declarative environment JSON contains duplicate keys"
              )
          result[key] = value
      return result


  def load_environment_json(path: Path) -> dict[str, str]:
      try:
          loaded = json.loads(
              path.read_text(encoding="utf-8-sig"),
              object_pairs_hook=reject_duplicate_json_keys,
          )
      except (OSError, UnicodeError, json.JSONDecodeError) as exc:
          raise ValueError(
              "declarative environment JSON cannot be read safely"
          ) from exc
      if not isinstance(loaded, dict):
          raise ValueError(
              "declarative environment JSON must be a mapping"
          )
      values: dict[str, str] = {}
      for key, value in loaded.items():
          if not isinstance(key, str) or not isinstance(value, str):
              raise ValueError(
                  "declarative environment keys and values must be strings"
              )
          values[key] = value
      return values


  def build_environment_payload(
      env_json: Path | None,
      env_files: list[Path],
  ) -> tuple[dict[str, str], bytes]:
      values = (
          load_environment_json(env_json)
          if env_json is not None
          else {}
      )
      for env_file in env_files:
          try:
              payload = env_file.read_bytes()
          except OSError as exc:
              raise ValueError(
                  f"declarative env file cannot be read: {env_file}"
              ) from exc
          values.update(_parse_env_bytes(payload))

      rendered = "".join(
          f"{key}={_encode_env_value(value)}\n"
          for key, value in sorted(values.items())
      ).encode("utf-8")
      # Validate the exact bytes that will be handed to the pair publisher.
      _parse_env_bytes(rendered)
      return values, rendered


  def drop_privileges(uid: int | None, gid: int | None) -> None:
      if (uid is None) != (gid is None):
          raise ValueError(
              "--run-as-uid and --run-as-gid must be provided together"
          )
      if uid is None or gid is None:
          return
      if uid < 0 or gid < 0:
          raise ValueError("run-as uid and gid must be non-negative")
      if os.geteuid() == uid and os.getegid() == gid:
          return
      if os.geteuid() != 0:
          raise PermissionError(
              "only root can drop the Nix activation writer identity"
          )
      os.setgroups([])
      if hasattr(os, "setresgid"):
          os.setresgid(gid, gid, gid)
      else:
          os.setgid(gid)
      if hasattr(os, "setresuid"):
          os.setresuid(uid, uid, uid)
      else:
          os.setuid(uid)
      if (
          os.getuid() != uid
          or os.geteuid() != uid
          or os.getgid() != gid
          or os.getegid() != gid
      ):
          raise RuntimeError("Nix activation privilege drop did not stick")


  def reconciled_config(
      current: Mapping[str, Any],
      declared: Mapping[str, Any],
      *,
      replace: bool,
  ) -> tuple[dict[str, Any], set[str]]:
      desired = (
          copy.deepcopy(dict(declared))
          if replace
          else deep_merge(current, declared)
      )
      before_epochs = {
          capability: capability_config_epoch(current, capability)
          for capability in CAPABILITIES
      }
      reconcile_capability_config_epochs(current, desired)
      config_advanced = {
          capability
          for capability in CAPABILITIES
          if capability_config_epoch(desired, capability)
          > before_epochs[capability]
      }
      return desired, config_advanced


  def publish_config_only(
      *,
      declared: Mapping[str, Any],
      config_path: Path,
      replace: bool,
  ) -> None:
      def publish(current: dict[str, Any]) -> None:
          desired, _config_advanced = reconciled_config(
              current,
              declared,
              replace=replace,
          )
          current.clear()
          current.update(desired)

      mutate_config_strict(publish, config_path=config_path)


  def publish_config_and_environment(
      *,
      declared: Mapping[str, Any],
      desired_env: Mapping[str, str],
      env_payload: bytes,
      config_path: Path,
      replace: bool,
  ) -> None:
      # The outer lock makes snapshot, epoch calculation, and pair publication
      # one serialized state transition.  Nested canonical calls reuse it.
      with credential_transaction(config_path):
          snapshot = load_credential_snapshot(config_path)
          expected_config = copy.deepcopy(snapshot.config)
          current_env = dict(snapshot.env)
          changed_env_keys = {
              key
              for key in set(current_env) | set(desired_env)
              if current_env.get(key) != desired_env.get(key)
          }

          def publish(current: dict[str, Any]) -> None:
              if current != expected_config:
                  raise RuntimeError(
                      "Hermes config changed during NixOS activation"
                  )
              desired, config_advanced = reconciled_config(
                  current,
                  declared,
                  replace=replace,
              )

              secret_capabilities: set[str] = set()
              for env_key in changed_env_keys:
                  secret_capabilities.update(
                      capability_epochs_for_secret_env(
                          current,
                          env_key,
                          env_values=desired_env,
                      )
                  )
                  secret_capabilities.update(
                      capability_epochs_for_secret_env(
                          desired,
                          env_key,
                          env_values=desired_env,
                      )
                  )
              secret_only_capabilities = sorted(
                  secret_capabilities - config_advanced
              )
              if secret_only_capabilities:
                  bump_capability_config_epochs(
                      desired,
                      *secret_only_capabilities,
                  )

              current.clear()
              current.update(desired)

          replace_config_env_payload_strict(
              publish,
              env_payload,
              config_path=config_path,
              env_keys=tuple(
                  sorted(set(current_env) | set(desired_env))
              ),
          )


  def main() -> None:
      args = parse_args()

      # Read and validate every declarative input before entering any writer.
      # A missing secret file therefore cannot leave config.yaml half-updated.
      declared = load_declared_config(args.source)
      manages_environment = (
          args.env_json is not None or bool(args.env_file)
      )
      if manages_environment:
          desired_env, env_payload = build_environment_payload(
              args.env_json,
              args.env_file,
          )
      else:
          desired_env, env_payload = {}, b""

      # Root may be the only identity allowed to read environmentFiles and to
      # migrate a legacy private lock.  Hold the canonical shared lock first,
      # then drop permanently so published targets are service-owned.  Nested
      # writers reuse this pinned transaction and its frozen access policy.
      with credential_transaction(args.config_path):
          drop_privileges(args.run_as_uid, args.run_as_gid)
          if manages_environment:
              publish_config_and_environment(
                  declared=declared,
                  desired_env=desired_env,
                  env_payload=env_payload,
                  config_path=args.config_path,
                  replace=args.replace,
              )
          else:
              publish_config_only(
                  declared=declared,
                  config_path=args.config_path,
                  replace=args.replace,
              )


  if __name__ == "__main__":
      main()
''
