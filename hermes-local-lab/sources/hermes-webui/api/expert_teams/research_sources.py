"""Trusted research-source adapters and deterministic fallback orchestration."""

from __future__ import annotations

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


class ResearchSourceAdapter(Protocol):
    async def probe(self) -> ProbeResult: ...

    async def search(self, query: str, *, limit: int = 5) -> AdapterSearchResult: ...

    async def materialize(self, hit: ResearchSourceHit, *, workspace: Path, run_id: str) -> dict: ...


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


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
        minimum_usable_hits: int = 2,
        clock: Callable[[], str] | None = None,
    ):
        self._search = search_callable or _default_web_search
        self._extract = extract_callable or _default_web_extract
        self._minimum_usable_hits = max(1, int(minimum_usable_hits))
        self._clock = clock or _now_iso

    async def probe(self) -> ProbeResult:
        return ProbeResult(available=callable(self._search) and callable(self._extract))

    async def search(self, query: str, *, limit: int = 5) -> AdapterSearchResult:
        try:
            payload = _decode_payload(await _maybe_await(self._search(str(query), limit=max(1, int(limit)))))
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
                    await _maybe_await(
                        self._extract([url], format="markdown", use_llm_processing=False)
                    )
                )
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

        coverage = "sufficient" if len(hits) >= self._minimum_usable_hits else ("insufficient" if hits else "none")
        if hits and failures:
            return AdapterSearchResult(
                hits=tuple(hits),
                coverage=coverage,
                status="partial",
                reason_code="partial_success",
                safe_reason=_SAFE_REASONS["partial_success"],
            )
        if hits:
            code = "" if coverage == "sufficient" else "insufficient_evidence"
            return AdapterSearchResult(
                hits=tuple(hits),
                coverage=coverage,
                status="success",
                reason_code=code,
                safe_reason=_SAFE_REASONS.get(code, ""),
            )
        code = failures[0] if failures and len(set(failures)) == 1 else "fetch_failed"
        return AdapterSearchResult(status="failed", reason_code=code, safe_reason=_SAFE_REASONS[code])

    async def materialize(self, hit: ResearchSourceHit, *, workspace: Path, run_id: str) -> dict:
        if hit.source_kind != "approved_public":
            raise ValueError("web adapter only materializes approved_public hits")
        return materialize_approved_source(
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
        minimum_usable_hits: int = 1,
        max_results: int = 20,
        max_files_scanned: int = 500,
        max_file_bytes: int = 2 * 1024 * 1024,
        clock: Callable[[], str] | None = None,
    ):
        self._roots = tuple(Path(root).expanduser() for root in roots)
        self._workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else None
        self._minimum_usable_hits = max(1, int(minimum_usable_hits))
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
            if root == home or root in home.parents or (self._workspace_root and self._workspace_root.is_relative_to(root)):
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

    async def search(self, query: str, *, limit: int = 5) -> AdapterSearchResult:
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
        scanned = 0
        result_limit = min(max(1, int(limit)), self._max_results)
        for root_index, root in enumerate(roots):
            for candidate in sorted(root.rglob("*")):
                if scanned >= self._max_files_scanned or len(hits) >= result_limit:
                    break
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                scanned += 1
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
            if scanned >= self._max_files_scanned or len(hits) >= result_limit:
                break

        if not hits:
            return AdapterSearchResult()
        coverage = "sufficient" if len(hits) >= self._minimum_usable_hits else "insufficient"
        code = "" if coverage == "sufficient" else "insufficient_evidence"
        return AdapterSearchResult(
            hits=tuple(hits),
            coverage=coverage,
            status="success",
            reason_code=code,
            safe_reason=_SAFE_REASONS.get(code, ""),
        )

    async def materialize(self, hit: ResearchSourceHit, *, workspace: Path, run_id: str) -> dict:
        if hit.source_kind != "approved_internal":
            raise ValueError("local adapter only materializes approved_internal hits")
        return materialize_approved_source(
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
    query: str,
    *,
    web_adapter: ResearchSourceAdapter,
    local_adapter: ResearchSourceAdapter,
    limit: int = 5,
) -> ResearchFallbackResult:
    web_probe = await _safe_probe(web_adapter)
    if web_probe.available:
        try:
            web = await web_adapter.search(query, limit=limit)
        except Exception as exc:
            code = classify_adapter_exception(exc)
            web = AdapterSearchResult(status="failed", reason_code=code, safe_reason=_SAFE_REASONS[code])
    else:
        code = web_probe.reason_code or "search_service_error"
        web = AdapterSearchResult(status="unavailable", reason_code=code, safe_reason=web_probe.safe_reason)

    public_hits = tuple(hit for hit in web.hits if hit.source_kind == "approved_public")
    local_hits: tuple[ResearchSourceHit, ...] = ()
    local = None
    if web.coverage != "sufficient":
        local_probe = await _safe_probe(local_adapter)
        if local_probe.available:
            try:
                local = await local_adapter.search(query, limit=limit)
                local_hits = tuple(hit for hit in local.hits if hit.source_kind == "approved_internal")
            except Exception as exc:
                code = classify_adapter_exception(exc)
                local = AdapterSearchResult(status="failed", reason_code=code, safe_reason=_SAFE_REASONS[code])

    combined = _deduplicate_hits((*public_hits, *local_hits))
    public_count = sum(hit.source_kind == "approved_public" for hit in combined)
    local_count = sum(hit.source_kind == "approved_internal" for hit in combined)
    sufficient = web.coverage == "sufficient" or (local is not None and local.coverage == "sufficient")
    model_count = 0 if sufficient else 1
    coverage = "sufficient" if sufficient else ("insufficient" if combined else "none")

    if model_count:
        reason_code = "insufficient_evidence" if combined else (web.reason_code or "no_results")
        return ResearchFallbackResult(
            hits=combined,
            public_count=public_count,
            local_count=local_count,
            model_count=1,
            coverage=coverage,
            safe_reason=_SAFE_REASONS.get(reason_code, _SAFE_REASONS["insufficient_evidence"]),
            reason_code=reason_code,
            status="model_fallback",
        )

    reason_code = web.reason_code
    return ResearchFallbackResult(
        hits=combined,
        public_count=public_count,
        local_count=local_count,
        model_count=0,
        coverage="sufficient",
        safe_reason=_SAFE_REASONS.get(reason_code, ""),
        reason_code=reason_code,
        status="ready",
    )
