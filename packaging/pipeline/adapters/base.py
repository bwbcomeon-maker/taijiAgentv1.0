"""The fixed adapter contract used by the common pipeline core."""


class CandidateAdapter:
    target_id = None
    artifact_kind = None
    success_label = None
    pending_label = None
    not_built_label = None
    online_plan_keys = ()

    def validate_target(self, payload):
        raise NotImplementedError

    def local_doctor(self, repo, target, state_root, *, ssh_config):
        raise NotImplementedError

    def inspect_input(self, repo, source_commit):
        raise NotImplementedError

    def build_plan(self, repo, target, state_root, *, run_id, ssh_config):
        raise NotImplementedError

    def bind_online_plan(self, plan, online):
        raise NotImplementedError

    def prepare_input(self, plan, command_runner):
        raise NotImplementedError

    def create_transport(self, repo, target, *, ssh_config, command_runner):
        raise NotImplementedError

    def validate_review(self, plan, review, remote_log):
        raise NotImplementedError

    def initial_state_patch(self, plan, online):
        raise NotImplementedError

    def success_state_patch(self, artifact):
        raise NotImplementedError

    def normalize_legacy_state(self, state):
        raise NotImplementedError
