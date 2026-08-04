"""Trusted research-source adapters and deterministic fallback orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import socket
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from .source_registry import materialize_approved_source


_ALLOWED_LOCAL_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}
_ALLOWED_SOURCE_KINDS = {"approved_public", "approved_internal"}
_SAFE_REASONS = {
    "data_egress_not_authorized": "当前研究请求未获准发送到公共检索服务",
    "policy_blocked": "目标站点的访问策略阻止了资料获取",
    "network_unreachable": "当前网络不可达，无法获取公共资料",
    "tls_error": "目标站点的安全连接校验失败",
    "timeout": "资料服务响应超时",
    "search_service_error": "公共资料检索服务暂时不可用",
    "no_results": "未找到与原始诉求相关的可核验资料",
    "fetch_failed": "检索到候选页面，但正文获取失败",
    "partial_success": "已保留成功获取的资料，部分候选页面未能读取",
    "insufficient_evidence": "现有资料不足以完整支撑研究结论",
}


def _safe_text(value: object, *, maximum: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_payload(payload: object) -> dict:
    if isinstance(payload, str):
        decoded = json.loads(payload)
    else:
        decoded = payload
    if not isinstance(decoded, dict):
        raise ValueError("adapter response must be an object")
    return decoded


def _classify_message(message: object, *, default: str) -> str:
    lowered = str(message or "").lower()
    if any(term in lowered for term in ("blocked", "policy", "forbidden", "robots")):
        return "policy_blocked"
    if any(term in lowered for term in ("tls", "ssl", "certificate")):
        return "tls_error"
    if any(term in lowered for term in ("timeout", "timed out")):
        return "timeout"
    if any(term in lowered for term in ("unreachable", "dns", "connection", "network")):
        return "network_unreachable"
    return default


def classify_adapter_exception(exc: BaseException, *, default: str = "search_service_error") -> str:
    if isinstance(exc, ssl.SSLError):
        return "tls_error"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, (ConnectionError, socket.gaierror)):
        return "network_unreachable"
    return _classify_message(exc, default=default)


@dataclass(frozen=True)
class ProbeResult:
    available: bool
    reason_code: str = ""
    safe_reason: str = ""


@dataclass(frozen=True)
class QueryAuthorizationDecision:
    authorized: bool
    safe_query: str = ""
    policy_id: str = ""
    trust_zone: str = ""
    reason_code: str = "data_egress_not_authorized"
    safe_reason: str = _SAFE_REASONS["data_egress_not_authorized"]


@dataclass(frozen=True)
class CoverageEvaluation:
    coverage: str
    covered_subquestions: tuple[str, ...] = ()
    reason_code: str = ""
    safe_reason: str = ""


@dataclass(frozen=True)
class ResearchSourceHit:
    source_kind: str
    safe_title: str
    locator: str
    content: str
    retrieved_at: str
    content_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_kind: str,
        safe_title: str,
        locator: str,
        content: str,
        retrieved_at: str,
    ) -> "ResearchSourceHit":
        if source_kind not in _ALLOWED_SOURCE_KINDS:
            raise ValueError("unsupported research source kind")
        normalized_content = str(content or "")
        if not normalized_content.strip():
            raise ValueError("research source content is empty")
        return cls(
            source_kind=source_kind,
            safe_title=_safe_text(safe_title) or "未命名资料",
            locator=str(locator or "").strip(),
            content=normalized_content,
            retrieved_at=str(retrieved_at or "").strip(),
            content_sha256=hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class AdapterSearchResult:
    hits: tuple[ResearchSourceHit, ...] = ()
    coverage: str = "none"
    status: str = "empty"
    reason_code: str = "no_results"
    safe_reason: str = _SAFE_REASONS["no_results"]


@dataclass(frozen=True)
class ResearchFallbackResult:
    hits: tuple[ResearchSourceHit, ...]
    public_count: int
    local_count: int
    model_count: int
    coverage: str
    safe_reason: str
    reason_code: str
    status: str
    public_status: dict
    local_status: dict
    tier_decisions: tuple[dict, ...]


class ResearchSourceAdapter(Protocol):
    async def probe(self) -> ProbeResult: ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        policy_decision: QueryAuthorizationDecision | None = None,
    ) -> AdapterSearchResult: ...

    async def materialize(self, hit: ResearchSourceHit, *, workspace: Path, run_id: str) -> dict: ...


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_without_blocking(callable_: Callable[..., object], *args, **kwargs) -> object:
    if inspect.iscoroutinefunction(callable_):
        return await callable_(*args, **kwargs)
    value = await asyncio.to_thread(callable_, *args, **kwargs)
    return await _maybe_await(value)


def _normalize_authorization(value: object) -> QueryAuthorizationDecision:
    if isinstance(value, QueryAuthorizationDecision):
        decision = value
    elif isinstance(value, dict):
        decision = QueryAuthorizationDecision(
            authorized=value.get("authorized") is True,
            safe_query=str(value.get("safe_query") or ""),
            policy_id=str(value.get("policy_id") or ""),
            trust_zone=str(value.get("trust_zone") or ""),
            reason_code=str(value.get("reason_code") or "data_egress_not_authorized"),
            safe_reason=str(value.get("safe_reason") or _SAFE_REASONS["data_egress_not_authorized"]),
        )
    else:
        decision = QueryAuthorizationDecision(False)
    safe_query = _safe_text(decision.safe_query, maximum=2000)
    policy_id = _safe_text(decision.policy_id, maximum=128)
    trust_zone = _safe_text(decision.trust_zone, maximum=128)
    if not decision.authorized:
        return QueryAuthorizationDecision(
            False,
            reason_code=_safe_text(decision.reason_code, maximum=128) or "data_egress_not_authorized",
            safe_reason=_safe_text(decision.safe_reason, maximum=500)
            or _SAFE_REASONS["data_egress_not_authorized"],
        )
    if not safe_query or not policy_id or not trust_zone:
        return QueryAuthorizationDecision(False)
    return QueryAuthorizationDecision(True, safe_query, policy_id, trust_zone, "", "")


async def _authorize_query(authorizer: Callable[[str], object] | None, query: str) -> QueryAuthorizationDecision:
    if authorizer is None:
        return QueryAuthorizationDecision(False)
    try:
        return _normalize_authorization(await _maybe_await(authorizer(str(query))))
    except Exception:
        return QueryAuthorizationDecision(False)


async def _evaluate_coverage(
    evaluator: Callable[[tuple[str, ...], tuple[ResearchSourceHit, ...]], object] | None,
    research_subquestions: tuple[str, ...],
    hits: tuple[ResearchSourceHit, ...],
) -> CoverageEvaluation:
    if evaluator is None or not research_subquestions:
        return CoverageEvaluation("insufficient" if hits else "none", reason_code="insufficient_evidence")
    try:
        value = await _maybe_await(evaluator(research_subquestions, hits))
    except Exception:
        return CoverageEvaluation("insufficient" if hits else "none", reason_code="insufficient_evidence")
    if isinstance(value, CoverageEvaluation):
        evaluation = value
    elif isinstance(value, dict):
        evaluation = CoverageEvaluation(
            coverage=str(value.get("coverage") or "insufficient"),
            covered_subquestions=tuple(str(item) for item in value.get("covered_subquestions") or ()),
            reason_code=str(value.get("reason_code") or ""),
            safe_reason=str(value.get("safe_reason") or ""),
        )
    else:
        evaluation = CoverageEvaluation("insufficient" if hits else "none")
    covered = tuple(dict.fromkeys(_safe_text(item, maximum=500) for item in evaluation.covered_subquestions if item))
    if evaluation.coverage == "sufficient" and hits and set(research_subquestions).issubset(set(covered)):
        return CoverageEvaluation("sufficient", covered, evaluation.reason_code, evaluation.safe_reason)
    coverage = "insufficient" if hits else "none"
    return CoverageEvaluation(
        coverage,
        covered,
        evaluation.reason_code or "insufficient_evidence",
        evaluation.safe_reason or _SAFE_REASONS["insufficient_evidence"],
    )


def _canonical_http_url(value: object) -> str | None:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = host
    if port and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _ensure_hermes_agent_path() -> None:
    configured = os.getenv("HERMES_WEBUI_AGENT_DIR", "").strip()
    sibling = Path(__file__).resolve().parents[3] / "hermes-agent"
    candidate = Path(configured).expanduser() if configured else sibling
    resolved = str(candidate.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def _default_web_search(query: str, limit: int = 5) -> object:
    _ensure_hermes_agent_path()
    from tools.web_tools import web_search_tool

    return web_search_tool(query, limit=limit)


async def _default_web_extract(urls: list[str], **kwargs) -> object:
    _ensure_hermes_agent_path()
    from tools.web_tools import web_extract_tool

    return await web_extract_tool(urls, **kwargs)


class HermesWebResearchSourceAdapter:
    def __init__(
        self,
        *,
        search_callable: Callable[..., object] | None = None,
        extract_callable: Callable[..., object] | None = None,
        query_authorizer: Callable[[str], object] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        self._search = search_callable or _default_web_search
        self._extract = extract_callable or _default_web_extract
        self._query_authorizer = query_authorizer
        self._clock = clock or _now_iso

    async def probe(self) -> ProbeResult:
        return ProbeResult(available=callable(self._search) and callable(self._extract))

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        policy_decision: QueryAuthorizationDecision | None = None,
    ) -> AdapterSearchResult:
        decision = _normalize_authorization(policy_decision) if policy_decision else await _authorize_query(
            self._query_authorizer,
            query,
        )
        if not decision.authorized:
            return AdapterSearchResult(
                status="denied",
                reason_code="data_egress_not_authorized",
                safe_reason=_SAFE_REASONS["data_egress_not_authorized"],
            )
        try:
            payload = _decode_payload(
                await _call_without_blocking(self._search, decision.safe_query, limit=max(1, int(limit)))
            )
        except Exception as exc:
            code = classify_adapter_exception(exc)
            return AdapterSearchResult(status="failed", reason_code=code, safe_reason=_SAFE_REASONS[code])

        if payload.get("success") is False or payload.get("error"):
            code = _classify_message(payload.get("error"), default="search_service_error")
            return AdapterSearchResult(status="failed", reason_code=code, safe_reason=_SAFE_REASONS[code])
        candidates = payload.get("data", {}).get("web", payload.get("results", []))
        if not isinstance(candidates, list):
            return AdapterSearchResult(
                status="failed",
                reason_code="search_service_error",
                safe_reason=_SAFE_REASONS["search_service_error"],
            )

        unique: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            url = _canonical_http_url(candidate.get("url"))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            unique.append((url, _safe_text(candidate.get("title")) or url))
            if len(unique) >= max(1, int(limit)):
                break
        if not unique:
            return AdapterSearchResult()

        hits: list[ResearchSourceHit] = []
        failures: list[str] = []
        for url, search_title in unique:
            try:
                extracted = _decode_payload(
                    await _call_without_blocking(
                        self._extract,
                        [url],
                        format="markdown",
                        use_llm_processing=False,
                    )
                )
                if extracted.get("success") is False or extracted.get("error"):
                    failures.append(
                        _classify_message(extracted.get("error"), default="search_service_error")
                    )
                    continue
                results = extracted.get("results", [])
                entry = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
                if entry.get("error") or entry.get("blocked_by_policy"):
                    failures.append(
                        _classify_message(
                            entry.get("error") or "blocked by policy",
                            default="fetch_failed",
                        )
                    )
                    continue
                content = str(entry.get("content") or entry.get("raw_content") or "")
                if not content.strip():
                    failures.append("fetch_failed")
                    continue
                hits.append(
                    ResearchSourceHit.create(
                        source_kind="approved_public",
                        safe_title=entry.get("title") or search_title,
                        locator=url,
                        content=content,
                        retrieved_at=self._clock(),
                    )
                )
            except Exception as exc:
                failures.append(classify_adapter_exception(exc, default="fetch_failed"))

        coverage = "unassessed" if hits else "none"
        if hits and failures:
            return AdapterSearchResult(
                hits=tuple(hits),
                coverage=coverage,
                status="partial",
                reason_code="partial_success",
                safe_reason=_SAFE_REASONS["partial_success"],
            )
        if hits:
            return AdapterSearchResult(
                hits=tuple(hits),
                coverage=coverage,
                status="success",
                reason_code="",
                safe_reason="",
            )
        code = failures[0] if failures and len(set(failures)) == 1 else "fetch_failed"
        return AdapterSearchResult(status="failed", reason_code=code, safe_reason=_SAFE_REASONS[code])

    async def materialize(self, hit: ResearchSourceHit, *, workspace: Path, run_id: str) -> dict:
        if hit.source_kind != "approved_public":
            raise ValueError("web adapter only materializes approved_public hits")
        return await asyncio.to_thread(
            materialize_approved_source,
            workspace,
            run_id,
            kind=hit.source_kind,
            label=hit.safe_title,
            origin_locator=hit.locator,
            content=hit.content,
            retrieved_at=hit.retrieved_at,
            content_sha256=hit.content_sha256,
        )


class LocalTextResearchSourceAdapter:
    def __init__(
        self,
        *,
        roots: Sequence[Path],
        workspace_root: Path | None = None,
        max_results: int = 20,
        max_files_scanned: int = 500,
        max_file_bytes: int = 2 * 1024 * 1024,
        clock: Callable[[], str] | None = None,
    ):
        self._roots = tuple(Path(root).expanduser() for root in roots)
        self._workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else None
        self._max_results = max(1, int(max_results))
        self._max_files_scanned = max(1, int(max_files_scanned))
        self._max_file_bytes = max(1, int(max_file_bytes))
        self._clock = clock or _now_iso

    def _approved_roots(self) -> tuple[Path, ...]:
        approved = []
        home = Path.home().resolve()
        for raw_root in self._roots:
            if raw_root.is_symlink() or not raw_root.is_dir():
                continue
            root = raw_root.resolve()
            if root == home or root in home.parents:
                continue
            if (
                self._workspace_root
                and root != self._workspace_root
                and self._workspace_root.is_relative_to(root)
            ):
                continue
            approved.append(root)
        return tuple(approved)

    async def probe(self) -> ProbeResult:
        available = bool(self._approved_roots())
        return ProbeResult(
            available=available,
            reason_code="" if available else "no_results",
            safe_reason="" if available else "未配置可访问的内部资料目录",
        )

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        policy_decision: QueryAuthorizationDecision | None = None,
    ) -> AdapterSearchResult:
        return await asyncio.to_thread(self._search_sync, query, limit)

    def _bounded_files(self, roots: tuple[Path, ...]):
        visited_entries = 0
        for root_index, root in enumerate(roots):
            pending = [root]
            while pending and visited_entries < self._max_files_scanned:
                directory = pending.pop()
                try:
                    iterator = os.scandir(directory)
                except OSError:
                    continue
                with iterator:
                    for entry in iterator:
                        visited_entries += 1
                        if visited_entries > self._max_files_scanned:
                            return
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name in {".git", ".taiji", ".venv", "venv", "node_modules"}:
                                    continue
                                pending.append(Path(entry.path))
                                continue
                            if entry.is_file(follow_symlinks=False):
                                yield root_index, root, Path(entry.path)
                        except OSError:
                            continue

    def _search_sync(self, query: str, limit: int) -> AdapterSearchResult:
        roots = self._approved_roots()
        if not roots:
            return AdapterSearchResult(
                status="unavailable",
                reason_code="no_results",
                safe_reason="未配置可访问的内部资料目录",
            )
        terms = []
        for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", str(query)):
            lowered = token.lower()
            if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
                terms.extend(token[index : index + 2] for index in range(len(token) - 1))
            else:
                terms.append(lowered)
        if not terms:
            return AdapterSearchResult()

        hits: list[ResearchSourceHit] = []
        result_limit = min(max(1, int(limit)), self._max_results)
        for root_index, root, candidate in self._bounded_files(roots):
            if len(hits) >= result_limit:
                break
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved.suffix.lower() not in _ALLOWED_LOCAL_SUFFIXES:
                continue
            try:
                size = resolved.stat().st_size
                if size <= 0 or size > self._max_file_bytes:
                    continue
                data = resolved.read_bytes()
                if b"\x00" in data:
                    continue
                content = data.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lowered = content.lower()
            if not any(term in lowered for term in terms):
                continue
            relative = resolved.relative_to(root).as_posix()
            hits.append(
                ResearchSourceHit.create(
                    source_kind="approved_internal",
                    safe_title=resolved.name,
                    locator=f"local://{root_index}/{relative}",
                    content=content,
                    retrieved_at=self._clock(),
                )
            )

        if not hits:
            return AdapterSearchResult()
        return AdapterSearchResult(
            hits=tuple(hits),
            coverage="unassessed",
            status="success",
            reason_code="",
            safe_reason="",
        )

    async def materialize(self, hit: ResearchSourceHit, *, workspace: Path, run_id: str) -> dict:
        if hit.source_kind != "approved_internal":
            raise ValueError("local adapter only materializes approved_internal hits")
        return await asyncio.to_thread(
            materialize_approved_source,
            workspace,
            run_id,
            kind=hit.source_kind,
            label=hit.safe_title,
            origin_locator=hit.locator,
            content=hit.content,
            retrieved_at=hit.retrieved_at,
            content_sha256=hit.content_sha256,
        )


async def _safe_probe(adapter: ResearchSourceAdapter) -> ProbeResult:
    try:
        return await adapter.probe()
    except Exception as exc:
        code = classify_adapter_exception(exc)
        return ProbeResult(False, code, _SAFE_REASONS[code])


def _deduplicate_hits(hits: Sequence[ResearchSourceHit]) -> tuple[ResearchSourceHit, ...]:
    deduplicated = []
    seen_hashes = set()
    for hit in hits:
        if hit.content_sha256 in seen_hashes:
            continue
        seen_hashes.add(hit.content_sha256)
        deduplicated.append(hit)
    return tuple(deduplicated)


async def orchestrate_research_sources(
    query: str | Sequence[str],
    *,
    web_adapter: ResearchSourceAdapter,
    local_adapter: ResearchSourceAdapter,
    query_authorizer: Callable[[str], object] | None = None,
    research_subquestions: Sequence[str] = (),
    coverage_evaluator: Callable[[tuple[str, ...], tuple[ResearchSourceHit, ...]], object] | None = None,
    existing_hits: Sequence[ResearchSourceHit] = (),
    limit: int = 5,
) -> ResearchFallbackResult:
    queries = tuple(
        dict.fromkeys(
            _safe_text(item, maximum=500)
            for item in ((query,) if isinstance(query, str) else query)
            if _safe_text(item, maximum=500)
        )
    )
    if not queries:
        queries = ("",)
    required_subquestions = tuple(
        dict.fromkeys(_safe_text(item, maximum=500) for item in research_subquestions if _safe_text(item, maximum=500))
    )
    existing = _deduplicate_hits(
        tuple(hit for hit in existing_hits if hit.source_kind in _ALLOWED_SOURCE_KINDS)
    )
    existing_public = tuple(hit for hit in existing if hit.source_kind == "approved_public")
    existing_local = tuple(hit for hit in existing if hit.source_kind == "approved_internal")
    existing_public_evaluation = await _evaluate_coverage(
        coverage_evaluator,
        required_subquestions,
        existing_public,
    )
    decisions = [await _authorize_query(query_authorizer, item) for item in queries]
    decision = next((item for item in decisions if item.authorized), decisions[0])
    if existing_public_evaluation.coverage == "sufficient":
        web = AdapterSearchResult(
            hits=existing_public,
            coverage="sufficient",
            status="reused",
            reason_code="",
            safe_reason="已恢复的公共证据已覆盖研究问题",
        )
    elif not any(item.authorized for item in decisions):
        web = AdapterSearchResult(
            status="denied",
            reason_code=decision.reason_code or "data_egress_not_authorized",
            safe_reason=decision.safe_reason or _SAFE_REASONS["data_egress_not_authorized"],
        )
    else:
        web_probe = await _safe_probe(web_adapter)
        if not web_probe.available:
            code = web_probe.reason_code or "search_service_error"
            web = AdapterSearchResult(status="unavailable", reason_code=code, safe_reason=web_probe.safe_reason)
        else:
            web_hits = []
            web_failures = []
            web_safe_reasons = []
            for current in decisions:
                if not current.authorized:
                    web_failures.append(current.reason_code or "data_egress_not_authorized")
                    continue
                try:
                    searched = await web_adapter.search(
                        current.safe_query,
                        limit=limit,
                        policy_decision=current,
                    )
                    web_hits.extend(searched.hits)
                    if searched.status not in {"success", "ready", "reused"}:
                        web_failures.append(searched.reason_code or "search_service_error")
                        if searched.safe_reason:
                            web_safe_reasons.append(searched.safe_reason)
                except Exception as exc:
                    web_failures.append(classify_adapter_exception(exc))
            web = AdapterSearchResult(
                hits=_deduplicate_hits(tuple(web_hits)),
                status=("success" if web_hits and not web_failures else "partial" if web_hits else "failed"),
                reason_code=(web_failures[0] if web_failures else ""),
                safe_reason=(web_safe_reasons[0] if web_safe_reasons else _SAFE_REASONS.get(web_failures[0], "部分研究查询未取得结果") if web_failures else ""),
            )

    public_hits = _deduplicate_hits(
        (*existing_public, *(hit for hit in web.hits if hit.source_kind == "approved_public"))
    )
    public_evaluation = await _evaluate_coverage(coverage_evaluator, required_subquestions, public_hits)
    local_hits: tuple[ResearchSourceHit, ...] = existing_local
    local_evaluation = await _evaluate_coverage(coverage_evaluator, required_subquestions, local_hits)
    local = AdapterSearchResult(
        hits=local_hits,
        status="skipped",
        reason_code="not_needed",
        safe_reason="公共资料已覆盖研究问题，无需查询内部资料",
    )
    if public_evaluation.coverage != "sufficient":
        recovered_evaluation = await _evaluate_coverage(
            coverage_evaluator,
            required_subquestions,
            _deduplicate_hits((*public_hits, *existing_local)),
        )
        if recovered_evaluation.coverage == "sufficient":
            local = AdapterSearchResult(
                hits=existing_local,
                coverage="sufficient",
                status="reused",
                reason_code="",
                safe_reason="已恢复的证据已覆盖研究问题",
            )
        else:
            local_probe = await _safe_probe(local_adapter)
            if not local_probe.available:
                local = AdapterSearchResult(
                    hits=existing_local,
                    status="unavailable",
                    reason_code=local_probe.reason_code or "no_results",
                    safe_reason=local_probe.safe_reason or "未配置可访问的内部资料目录",
                )
            else:
                try:
                    local_results = []
                    local_failures = []
                    for current_query in queries:
                        try:
                            searched = await local_adapter.search(current_query, limit=limit)
                            local_results.extend(searched.hits)
                            if searched.status not in {"success", "ready", "reused"}:
                                local_failures.append(searched.reason_code or "no_results")
                        except Exception as exc:
                            local_failures.append(classify_adapter_exception(exc))
                    local_hits = _deduplicate_hits(
                        (*existing_local, *(hit for hit in local_results if hit.source_kind == "approved_internal"))
                    )
                    local = AdapterSearchResult(
                        hits=local_hits,
                        coverage="unassessed",
                        status=("success" if local_results and not local_failures else "partial" if local_results else "failed"),
                        reason_code=(local_failures[0] if local_failures else ""),
                        safe_reason=(_SAFE_REASONS.get(local_failures[0], "部分内部资料查询未取得结果") if local_failures else ""),
                    )
                    local_evaluation = await _evaluate_coverage(coverage_evaluator, required_subquestions, local_hits)
                except Exception as exc:
                    code = classify_adapter_exception(exc)
                    local = AdapterSearchResult(
                        hits=existing_local,
                        status="failed",
                        reason_code=code,
                        safe_reason=_SAFE_REASONS[code],
                    )

    combined = _deduplicate_hits((*public_hits, *local_hits))
    final_evaluation = (
        public_evaluation
        if public_evaluation.coverage == "sufficient"
        else await _evaluate_coverage(coverage_evaluator, required_subquestions, combined)
    )
    public_count = sum(hit.source_kind == "approved_public" for hit in combined)
    local_count = sum(hit.source_kind == "approved_internal" for hit in combined)
    sufficient = final_evaluation.coverage == "sufficient"
    model_count = 0 if sufficient else 1
    coverage = final_evaluation.coverage

    public_status = {
        "status": web.status,
        "reason": web.reason_code,
        "safe_reason": web.safe_reason,
        "count": public_count,
        "coverage": public_evaluation.coverage,
        "policy_id": decision.policy_id,
        "trust_zone": decision.trust_zone,
    }
    local_status = {
        "status": local.status,
        "reason": local.reason_code,
        "safe_reason": local.safe_reason,
        "count": local_count,
        "coverage": local_evaluation.coverage,
    }
    tier_decisions = [
        {"tier": "public", **public_status},
        {"tier": "local", **local_status},
    ]
    if model_count:
        tier_decisions.append(
            {
                "tier": "model",
                "status": "used",
                "reason": "insufficient_evidence",
                "safe_reason": _SAFE_REASONS["insufficient_evidence"],
                "count": 1,
                "coverage": coverage,
            }
        )

    if model_count:
        reason_code = "insufficient_evidence" if combined else (web.reason_code or local.reason_code or "no_results")
        status = "model_fallback"
    else:
        reason_code = web.reason_code
        status = "ready"
    return ResearchFallbackResult(
        hits=combined,
        public_count=public_count,
        local_count=local_count,
        model_count=model_count,
        coverage=coverage,
        safe_reason=_SAFE_REASONS.get(reason_code, web.safe_reason or local.safe_reason),
        reason_code=reason_code,
        status=status,
        public_status=public_status,
        local_status=local_status,
        tier_decisions=tuple(tier_decisions),
    )
