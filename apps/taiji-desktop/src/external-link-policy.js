function isTrustedExternalUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || ""));
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch (_) {
    return false;
  }
}

function createExternalWindowOpenHandler(openExternal, reportError = () => {}) {
  if (typeof openExternal !== "function") {
    throw new TypeError("openExternal must be a function");
  }
  return ({ url } = {}) => {
    const target = String(url || "");
    if (isTrustedExternalUrl(target)) {
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
  createExternalWindowOpenHandler,
};
