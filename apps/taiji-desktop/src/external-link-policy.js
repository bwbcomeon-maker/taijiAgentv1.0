function normalizeTrustedExternalOrigins(value, options = {}) {
  const entries = Array.isArray(value) ? value : String(value || "").split(",");
  const allowed = [];
  for (const entry of entries) {
    try {
      const parsed = new URL(String(entry || "").trim());
      const local = ["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsed.hostname);
      const secure = parsed.protocol === "https:";
      const localDevelopment = options.allowLocalHttp === true && parsed.protocol === "http:" && local;
      if (!secure && !localDevelopment) continue;
      if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) continue;
      if (!allowed.includes(parsed.origin)) allowed.push(parsed.origin);
    } catch (_) {
      // Invalid configuration is ignored so the policy remains fail-closed.
    }
  }
  return allowed;
}

function isTrustedExternalUrl(rawUrl, allowedOrigins = []) {
  try {
    const parsed = new URL(String(rawUrl || ""));
    if (parsed.username || parsed.password) return false;
    return Array.isArray(allowedOrigins) && allowedOrigins.includes(parsed.origin);
  } catch (_) {
    return false;
  }
}

function createExternalWindowOpenHandler(openExternal, reportError = () => {}, allowedOrigins = []) {
  if (typeof openExternal !== "function") {
    throw new TypeError("openExternal must be a function");
  }
  return ({ url } = {}) => {
    const target = String(url || "");
    if (isTrustedExternalUrl(target, allowedOrigins)) {
      try {
        Promise.resolve(openExternal(target)).catch(reportError);
      } catch (error) {
        reportError(error);
      }
    }
    return { action: "deny" };
  };
}

module.exports = {
  isTrustedExternalUrl,
  normalizeTrustedExternalOrigins,
  createExternalWindowOpenHandler,
};
