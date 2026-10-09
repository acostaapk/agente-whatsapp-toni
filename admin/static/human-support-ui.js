(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.HumanSupportUI = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  const ASSET_PATHS = Object.freeze({
    t1: "t1.jpg",
    t2: "t2.jpg",
    t3: "t3.jpg",
    t3_smart: "t3-smart.jpg",
  });
  const ASSET_ORIGIN = "https://toni.analisereview.com.br/static/machines/";

  function prepareReplyDraft(reply) {
    if (!reply || typeof reply.id !== "string" || typeof reply.text !== "string") {
      return null;
    }
    return { kind: "text", replyId: reply.id, text: reply.text };
  }

  function prepareImageDraft(asset) {
    if (!asset || !Object.hasOwn(ASSET_PATHS, asset.id)) return null;
    const previewUrl = ASSET_ORIGIN + ASSET_PATHS[asset.id];
    return {
      kind: "image",
      assetId: asset.id,
      name: String(asset.name || asset.id),
      alt: String(asset.alt || asset.name || asset.id),
      previewUrl,
      caption: "",
    };
  }

  function buildImageSendPayload(phone, pending, caption) {
    if (!phone || !pending || !Object.hasOwn(ASSET_PATHS, pending.assetId)) return null;
    return {
      to: String(phone),
      asset_id: pending.assetId,
      caption: String(caption || ""),
    };
  }

  function createRequestGate() {
    let latest = 0;
    return Object.freeze({
      next() {
        latest += 1;
        return latest;
      },
      isCurrent(requestId) {
        return requestId === latest;
      },
    });
  }

  function shouldClearTextDraft(currentDraft, currentVersion, submittedDraft, submittedVersion) {
    return currentVersion === submittedVersion && currentDraft === submittedDraft;
  }

  function shouldClearImageDraft(currentPending, submittedPending, submittedCaption) {
    return Boolean(
      currentPending &&
      currentPending === submittedPending &&
      currentPending.caption === submittedCaption
    );
  }

  function formatSupportTimestamp(ts) {
    if (typeof ts !== "number" || !isFinite(ts) || ts <= 0) return "";
    const parts = new Intl.DateTimeFormat("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/Sao_Paulo",
    }).formatToParts(new Date(ts * 1000));
    const get = (type) => (parts.find((p) => p.type === type) || {}).value || "";
    if (!get("day") || !get("month") || !get("hour") || !get("minute")) return "";
    return `${get("day")}/${get("month")} ${get("hour")}:${get("minute")}`;
  }

  function shouldPlayMessageSound(prevMaxTs, nextMaxTs, initialized) {
    return initialized === true && nextMaxTs > prevMaxTs;
  }

  return Object.freeze({
    prepareReplyDraft,
    prepareImageDraft,
    buildImageSendPayload,
    createRequestGate,
    shouldClearTextDraft,
    shouldClearImageDraft,
    formatSupportTimestamp,
    shouldPlayMessageSound,
  });
});
