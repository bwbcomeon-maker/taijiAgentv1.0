import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ROOT / "static" / "commands.js"
PORTAL = ROOT / "static" / "expert-team-v3.js"
PANELS = ROOT / "static" / "panels.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_portal_launches_only_the_selected_server_profile():
    script = _read(PORTAL)
    summon_start = script.index("async function summon")
    summon_body = script[summon_start : script.index("async function loadCatalog", summon_start)]

    assert "launch_profile_id" in summon_body
    assert "example.available" in summon_body
    assert "example.disabled_reason" in summon_body
    assert "new_session" not in summon_body
    assert "team_id:" not in summon_body
    assert "document_type:" not in summon_body


def test_portal_catalog_failure_is_fail_closed_and_recoverable():
    script = _read(PORTAL)
    load_start = script.index("async function loadCatalog")
    load_body = script[load_start : script.index("function progressHtml", load_start)]

    assert "fallbackTeams" not in script
    assert "state.catalog = []" in load_body
    assert "catalogStatus = 'error'" in load_body
    assert 'data-et3-action="retry-catalog"' in script
    assert "已显示本地团队" not in script


def test_catalog_entries_without_a_launch_profile_are_disabled_with_a_reason():
    script = _read(PORTAL)

    assert "example.available === true" in script
    assert "example.launch_profile_id" in script
    assert "disabled_reason" in script
    assert "disabled" in script
    assert "aria-disabled" in script


def test_expert_team_action_uses_one_atomic_launch_request_and_no_session_precreate():
    script = _read(COMMANDS)
    start = script.index("async function sendExpertTeamAction")
    body = script[start : script.index("if(typeof window!=='undefined')window.sendExpertTeamAction", start)]

    assert "'/api/expert-teams/launch'" in body
    assert "/api/expert-teams/start" not in body
    assert "newSession(" not in body
    assert "'/api/session/new'" not in body
    request_start = script.index("function _expertTeamLaunchRequest")
    request_body = script[request_start : script.index("function _adoptExpertTeamLaunchSession", request_start)]
    assert "launch_profile_id" in request_body
    assert "idempotency_key" in request_body
    assert "session_options" in request_body
    assert "JSON.stringify(request.body)" in body


def test_launch_session_options_are_a_strict_frontend_allowlist():
    script = _read(COMMANDS)
    start = script.index("function _expertTeamLaunchSessionOptions")
    body = script[start : script.index("function _expertTeamLaunchRequest", start)]

    for field in ("workspace", "profile", "project_id", "model", "model_provider"):
        assert field in body
    for forbidden in ("prev_session_id", "worktree", "session_id", "team_id"):
        assert forbidden not in body


def test_failed_launch_keeps_the_current_session_and_reuses_the_same_key():
    script = _read(COMMANDS)
    start = script.index("let _expertTeamPendingLaunch")
    end = script.index("if(typeof window!=='undefined')window.sendExpertTeamAction", start)
    snippet = script[start:end]
    source = textwrap.dedent(
        f"""
        global.window=global;
        global.S={{session:{{session_id:'existing',workspace:'/work'}},activeProfile:'default',busy:false}};
        global._activeProject='project-one';
        global.NO_PROJECT_FILTER='__all__';
        global.$=()=>null;
        global._readPersistedModelState=()=>({{model:'gpt-test',model_provider:'local'}});
        global.showToast=()=>{{}};
        global.renderSessionList=async()=>{{}};
        global.apiCalls=[];
        global.api=async(path,options)=>{{
          apiCalls.push([path,JSON.parse(options.body)]);
          const error=new Error('offline'); error.status=503; throw error;
        }};
        {snippet}
        (async()=>{{
          const payload={{launch_profile_id:'content-work-report',prompt:'same prompt'}};
          const first=await sendExpertTeamAction(payload);
          const second=await sendExpertTeamAction(payload);
          console.log(JSON.stringify({{
            first,second,
            session:S.session.session_id,
            paths:apiCalls.map(item=>item[0]),
            keys:apiCalls.map(item=>item[1].idempotency_key),
            bodies:apiCalls.map(item=>Object.keys(item[1]).sort()),
            options:apiCalls[0][1].session_options,
          }}));
        }})().catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    completed = subprocess.run(
        ["node", "-e", source], cwd=ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["first"] is False
    assert result["second"] is False
    assert result["session"] == "existing"
    assert result["paths"] == ["/api/expert-teams/launch", "/api/expert-teams/launch"]
    assert result["keys"][0] == result["keys"][1]
    assert result["options"] == {
        "workspace": "/work",
        "profile": "default",
        "project_id": "project-one",
        "model": "gpt-test",
        "model_provider": "local",
    }
    assert result["bodies"] == [
        ["idempotency_key", "launch_profile_id", "prompt", "session_options"],
        ["idempotency_key", "launch_profile_id", "prompt", "session_options"],
    ]


def test_committed_launch_stays_successful_when_sidebar_refresh_fails():
    script = _read(COMMANDS)
    start = script.index("let _expertTeamPendingLaunch")
    end = script.index("if(typeof window!=='undefined')window.sendExpertTeamAction", start)
    snippet = script[start:end]
    source = textwrap.dedent(
        f"""
        global.window=global;
        global.S={{session:{{session_id:'existing',workspace:'/work'}},activeProfile:'default',busy:false,messages:[]}};
        global._activeProject='';
        global.NO_PROJECT_FILTER='__all__';
        global.$=()=>null;
        global._readPersistedModelState=()=>null;
        global.showToast=()=>{{}};
        global._expertTeamStatusCardFromRun=(run)=>({{kind:'expert_team',runId:run.run_id}});
        global.renderSessionList=async()=>{{throw new Error('sidebar unavailable');}};
        global.api=async()=>({{
          ok:true,
          run:{{run_id:'run-new'}},
          session:{{session_id:'session-new',workspace:'/work',messages:[{{role:'user',content:'prompt'}}]}},
          session_messages:[{{role:'user',content:'prompt'}}],
        }});
        {snippet}
        (async()=>{{
          const result=await sendExpertTeamAction({{launch_profile_id:'content-work-report',prompt:'prompt'}});
          console.log(JSON.stringify({{result,session:S.session.session_id,messages:S.messages.length}}));
        }})().catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    completed = subprocess.run(
        ["node", "-e", source], cwd=ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "result": True,
        "session": "session-new",
        "messages": 1,
    }


def test_failed_launch_key_survives_a_renderer_reload_boundary():
    script = _read(COMMANDS)
    start = script.index("let _expertTeamPendingLaunch")
    end = script.index("if(typeof window!=='undefined')window.sendExpertTeamAction", start)
    snippet = script[start:end]
    source = textwrap.dedent(
        f"""
        global.window=global;
        const store=new Map();
        global.localStorage={{
          getItem:key=>store.has(key)?store.get(key):null,
          setItem:(key,value)=>store.set(key,String(value)),
          removeItem:key=>store.delete(key),
        }};
        global.S={{session:{{session_id:'existing',workspace:'/work'}},activeProfile:'default',busy:false}};
        global._activeProject='';
        global.NO_PROJECT_FILTER='__all__';
        global.$=()=>null;
        global._readPersistedModelState=()=>null;
        global.showToast=()=>{{}};
        global.renderSessionList=async()=>{{}};
        const keys=[];
        global.api=async(_path,options)=>{{
          keys.push(JSON.parse(options.body).idempotency_key);
          throw new Error('connection lost');
        }};
        {snippet}
        (async()=>{{
          const payload={{launch_profile_id:'content-work-report',prompt:'same prompt'}};
          await sendExpertTeamAction(payload);
          _expertTeamPendingLaunch=null;
          await sendExpertTeamAction(payload);
          console.log(JSON.stringify({{keys,stored:store.size,storedValue:[...store.values()][0]||''}}));
        }})().catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    completed = subprocess.run(
        ["node", "-e", source], cwd=ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["keys"][0] == result["keys"][1]
    assert result["stored"] == 1
    assert "same prompt" not in result["storedValue"]


def test_legacy_portal_payload_carries_the_server_launch_profile_without_new_session_flag():
    script = _read(PANELS)
    payload_start = script.index("function _writeflowExpertTeamStartPayload")
    payload_body = script[
        payload_start : script.index("async function summonWriteflowTeam", payload_start)
    ]
    summon_start = script.index("async function summonWriteflowTeam")
    summon_body = script[summon_start : script.index("function _writeflowModeLabel", summon_start)]

    assert "launch_profile_id" in payload_body
    assert "new_session: true" not in summon_body
    assert "team_id:team.id" not in payload_body
