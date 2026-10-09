const assert = require("node:assert/strict");
const test = require("node:test");
const ui = require("../static/human-support-ui.js");

test("preparing a reply only produces editable draft text", () => {
  const reply = { id: "acolhimento", text: "Entendi. Vou verificar isso." };

  assert.deepEqual(ui.prepareReplyDraft(reply), {
    kind: "text",
    replyId: "acolhimento",
    text: "Entendi. Vou verificar isso.",
  });
});

test("preparing an image returns preview state without a send URL", () => {
  const asset = {
    id: "t2",
    name: "T2",
    alt: "Maquininha Ton T2",
    preview_url: "https://toni.analisereview.com.br/static/machines/t2.jpg",
  };

  assert.deepEqual(ui.prepareImageDraft(asset), {
    kind: "image",
    assetId: "t2",
    name: "T2",
    alt: "Maquininha Ton T2",
    previewUrl: "https://toni.analisereview.com.br/static/machines/t2.jpg",
    caption: "",
  });
});

test("image send payload carries only the allowlisted asset ID, not its URL", () => {
  const pending = ui.prepareImageDraft({
    id: "t3_smart",
    name: "T3 Smart",
    alt: "Maquininha Ton T3 Smart",
    preview_url: "https://toni.analisereview.com.br/static/machines/t3-smart.jpg",
  });

  const payload = ui.buildImageSendPayload("15555550001", pending, "Confira a T3 Smart.");

  assert.deepEqual(payload, {
    to: "15555550001",
    asset_id: "t3_smart",
    caption: "Confira a T3 Smart.",
  });
  assert.equal(Object.hasOwn(payload, "url"), false);
});

test("a newer conversation selection invalidates older load responses", () => {
  const gate = ui.createRequestGate();
  const oldConversationRequest = gate.next();
  const newConversationRequest = gate.next();

  assert.equal(gate.isCurrent(oldConversationRequest), false);
  assert.equal(gate.isCurrent(newConversationRequest), true);
});

test("a completed text send clears only the exact draft version it submitted", () => {
  assert.equal(ui.shouldClearTextDraft("Sent text", 4, "Sent text", 4), true);
  assert.equal(ui.shouldClearTextDraft("Next draft", 5, "Sent text", 4), false);
  assert.equal(ui.shouldClearTextDraft("Next draft", 4, "Sent text", 4), false);
});

test("a completed image send preserves a replacement image or caption draft", () => {
  const submitted = {assetId: "t1", caption: "Old caption"};
  assert.equal(ui.shouldClearImageDraft(submitted, submitted, "Old caption"), true);
  submitted.caption = "New caption";
  assert.equal(ui.shouldClearImageDraft(submitted, submitted, "Old caption"), false);
  const replacement = {assetId: "t2", caption: "New image"};
  assert.equal(ui.shouldClearImageDraft(replacement, submitted, "Old caption"), false);
});

test("support timestamps format as dd/mm hh:mm and ignore invalid values", () => {
  assert.match(ui.formatSupportTimestamp(1791412840.278), /^\d{2}\/\d{2} \d{2}:\d{2}$/);
  assert.equal(ui.formatSupportTimestamp(null), "");
  assert.equal(ui.formatSupportTimestamp(0), "");
  assert.equal(ui.formatSupportTimestamp("abc"), "");
});

test("message sound only fires after init when max timestamp grows", () => {
  assert.equal(ui.shouldPlayMessageSound(100, 101, true), true);
  assert.equal(ui.shouldPlayMessageSound(100, 101, false), false);
  assert.equal(ui.shouldPlayMessageSound(100, 100, true), false);
});
