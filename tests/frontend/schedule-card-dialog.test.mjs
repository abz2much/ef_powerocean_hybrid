// The card's form while a change is being sent, and the card's editor.
// Run with: node --test tests/frontend/
import { test } from "node:test";
import assert from "node:assert/strict";

const defined = {};
globalThis.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = {};
    return this.shadowRoot;
  }
  dispatchEvent() {}
};
globalThis.customElements = {
  define: (name, element) => {
    defined[name] = element;
  },
  get: (name) => defined[name],
};
globalThis.window = { customCards: [] };

await import(
  "../../custom_components/ef_powerocean_hybrid/frontend/schedule-card.js"
);

const Card = defined["ef-powerocean-schedule-card"];
const Editor = defined["ef-powerocean-schedule-card-editor"];

// The parts of the form _renderMessages fills in.
function fakeDialog() {
  const server = { dataset: { msg: "server" }, textContent: "" };
  const save = { disabled: false };
  const buttons = [{ disabled: false }, { disabled: false }];
  return {
    open: true,
    server,
    save,
    close() {
      this.open = false;
    },
    querySelector(selector) {
      if (selector === ".save") return save;
      return { textContent: "" };
    },
    querySelectorAll(selector) {
      return selector === "[data-msg]" ? [server] : buttons;
    },
  };
}

function cardWithForm() {
  const card = new Card();
  const dialog = fakeDialog();
  card.shadowRoot.querySelector = () => dialog;
  card._render = () => {};
  card._errors = () => ({});
  card._original = null; // a new schedule: nothing else to change
  card._draft = { start: 120, end: 180 };
  return { card, dialog };
}

test("the form says why Save waits while a change is being sent", () => {
  const { card, dialog } = cardWithForm();
  card._busy = true;

  card._renderMessages();

  assert.equal(dialog.save.disabled, true);
  assert.match(dialog.server.textContent, /last change/);
});

test("an open form can save again once the change is done", async () => {
  const { card, dialog } = cardWithForm();
  let finish;
  card._hass = {
    callService: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  };

  const call = card._call({ domain: "d", service: "s" }, "Saving");
  // Another schedule opened while the first change is being sent
  dialog.open = true;
  card._renderMessages();
  assert.equal(dialog.save.disabled, true);
  finish();
  await call;

  assert.equal(dialog.save.disabled, false);
  assert.equal(dialog.server.textContent, "");
});

function fakeEditor() {
  const editor = new Editor();
  let renders = 0;
  Object.defineProperty(editor, "innerHTML", {
    set() {
      renders += 1;
    },
  });
  editor.querySelector = () => ({ addEventListener() {} });
  return { editor, renders: () => renders };
}

function hassWith(...ids) {
  const entities = {};
  for (const id of ids) {
    entities[id] = {
      entity_id: id,
      platform: "ef_powerocean_hybrid",
      translation_key: "active_schedule",
    };
  }
  return { entities, states: {} };
}

test("the editor keeps its list while nothing in it changes", () => {
  // Rebuilding it on every update closes an open dropdown.
  const { editor, renders } = fakeEditor();
  editor.setConfig({ entity: "sensor.a_active_schedule" });

  editor.hass = hassWith("sensor.a_active_schedule");
  editor.hass = hassWith("sensor.a_active_schedule");

  assert.equal(renders(), 1);
});

test("the editor shows a new system", () => {
  const { editor, renders } = fakeEditor();
  editor.setConfig({ entity: "sensor.a_active_schedule" });
  editor.hass = hassWith("sensor.a_active_schedule");

  editor.hass = hassWith(
    "sensor.a_active_schedule",
    "sensor.b_active_schedule",
  );

  assert.equal(renders(), 2);
});
