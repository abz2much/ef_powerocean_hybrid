// The EcoFlow app's scheduled tasks as a dashboard card: a 24-hour bar of
// today's schedules, the list with on/off switches, and a pop-up form to add,
// change and delete them. It uses the integration's actions, which check
// everything again; nothing is sent before Save.
import {
  addData,
  barPieces,
  changedFields,
  checkDraft,
  findSchedules,
  firstFreeWindow,
  runsOn,
  toHm,
  viewKey,
  weekdayIn,
} from "./schedule-logic.js?v=0.3.0"; // the manifest version (tested)

const DOMAIN = "ef_powerocean_hybrid";
const CARD = "ef-powerocean-schedule-card";
const EDITOR = "ef-powerocean-schedule-card-editor";
const DAYS = [
  ["mon", "Mo"],
  ["tue", "Tu"],
  ["wed", "We"],
  ["thu", "Th"],
  ["fri", "Fr"],
  ["sat", "Sa"],
  ["sun", "Su"],
];
const DAY_NAMES = {
  mon: "Mon",
  tue: "Tue",
  wed: "Wed",
  thu: "Thu",
  fri: "Fri",
  sat: "Sat",
  sun: "Sun",
};
const TIMES = Array.from({ length: 48 }, (_, i) => i * 30);

function escape(text) {
  return String(text).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}

function daysText(days) {
  return days === "daily"
    ? "every day"
    : days.map((d) => DAY_NAMES[d] ?? d).join(", ");
}

// The Active schedule sensors of this integration, one per system.
function candidates(hass) {
  return Object.values(hass?.entities ?? {})
    .filter(
      (e) =>
        e.platform === DOMAIN &&
        e.translation_key === "active_schedule" &&
        e.entity_id.startsWith("sensor."),
    )
    .map((e) => e.entity_id);
}

const STYLE = `
  :host { --chg: #1e88e5; --bat: #43a047; }
  .card { padding: 16px; color: var(--primary-text-color); }
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .head h2 { margin: 0; font-size: 1.2em; font-weight: 500; }
  button { font: inherit; cursor: pointer; }
  .add { border: none; border-radius: 16px; padding: 4px 14px; color: #fff; background: var(--primary-color); }
  .add[disabled] { opacity: .4; cursor: default; }
  .hint { color: var(--secondary-text-color); font-size: .85em; }
  .bar { position: relative; height: 26px; border-radius: 6px; overflow: hidden;
         background: var(--divider-color); margin-top: 4px; }
  .piece { position: absolute; top: 0; bottom: 0; border: none; padding: 0;
           box-shadow: inset -1px 0 var(--card-background-color); }
  .charge { background: var(--chg); } .use_battery { background: var(--bat); }
  .piece.off, .row.off .dot { opacity: .35; }
  .piece.now { outline: 2px solid #fff; outline-offset: -2px; }
  .ticks { display: flex; justify-content: space-between; color: var(--secondary-text-color);
           font-size: .75em; margin: 2px 0 6px; }
  .row { display: flex; align-items: center; gap: 10px; padding: 8px 0;
         border-top: 1px solid var(--divider-color); cursor: pointer; }
  .row.later { opacity: .5; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .grow { flex: 1; min-width: 0; }
  .now-label { font-size: .75em; padding: 1px 6px; border-radius: 8px; margin-left: 4px;
               background: var(--primary-color); color: #fff; }
  .toggle { width: 36px; height: 20px; border-radius: 10px; border: none; position: relative;
            background: var(--divider-color); flex: none; }
  .toggle::after { content: ""; position: absolute; top: 3px; left: 3px; width: 14px; height: 14px;
                   border-radius: 50%; background: #fff; transition: left .15s; }
  .toggle.on { background: var(--primary-color); } .toggle.on::after { left: 19px; }
  dialog { border: none; border-radius: 12px; padding: 20px; width: min(420px, 90vw);
           background: var(--card-background-color); color: var(--primary-text-color); }
  dialog::backdrop { background: rgba(0, 0, 0, .5); }
  dialog h3 { margin: 0 0 12px; font-weight: 500; }
  .field { margin: 10px 0; }
  .field > label { display: block; color: var(--secondary-text-color); font-size: .85em; margin-bottom: 4px; }
  .seg { display: flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .seg button { flex: 1; border: none; padding: 7px 0; background: none; color: inherit; }
  .seg button.sel.charge { background: var(--chg); color: #fff; }
  .seg button.sel.use_battery { background: var(--bat); color: #fff; }
  input, select { font: inherit; width: 100%; box-sizing: border-box; padding: 7px 8px; border-radius: 6px;
                  border: 1px solid var(--divider-color); background: none; color: inherit; }
  select option { color: #000; }
  .two { display: flex; gap: 10px; } .two > .field { flex: 1; }
  .chips button { border: none; border-radius: 15px; padding: 5px 9px; margin: 2px 1px;
                  background: var(--divider-color); color: inherit; }
  .chips button.sel { background: var(--primary-color); color: #fff; }
  .error { color: var(--error-color); font-size: .85em; margin-top: 3px; }
  .buttons { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; }
  .buttons button { border: none; border-radius: 16px; padding: 6px 14px; background: none; }
  .delete { color: var(--error-color); }
  .cancel { color: var(--primary-color); }
  .save { background: var(--primary-color) !important; color: #fff; }
  .save[disabled] { opacity: .4; cursor: default; }
`;

class ScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._draft = null;
    this._original = null;
    this._serverError = "";
    this._busy = false;
  }

  static getConfigElement() {
    return document.createElement(EDITOR);
  }

  static getStubConfig(hass) {
    return { entity: candidates(hass)[0] ?? "" };
  }

  setConfig(config) {
    if (!config?.entity)
      throw new Error("Choose the system's Active schedule sensor.");
    this._config = config;
    this._shownKey = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 5;
  }

  get _data() {
    return findSchedules(this._hass, this._config.entity);
  }

  _render() {
    if (!this._hass || !this._config) return;
    const root = this.shadowRoot;
    if (!root.querySelector("ha-card")) {
      root.innerHTML = `<style>${STYLE}</style><ha-card><div class="card"></div></ha-card><dialog></dialog>`;
      root.addEventListener("click", (event) => this._onClick(event));
      root.addEventListener("change", (event) => this._onChange(event));
      root.addEventListener("input", (event) => this._onChange(event));
      root
        .querySelector("dialog")
        .addEventListener("close", () => this._closed());
    }
    // Only when what the card shows changed: a redraw between pointer-down
    // and click would drop the tap.
    const key = viewKey(this._hass, this._config.entity, this._today());
    if (key === this._shownKey) return;
    this._shownKey = key;
    root.querySelector(".card").innerHTML = this._cardHtml(this._data);
  }

  _today() {
    return weekdayIn(this._hass.config.time_zone, new Date());
  }

  _cardHtml({ available, schedules, maxPower }) {
    if (!available) {
      return `<div class="head"><h2>Schedules</h2></div>
        <div class="hint">Schedules not available. They need the Extended connection (MQTT).</div>`;
    }
    const today = this._today();
    const full = schedules.length >= 6;
    const noMax = maxPower === null;
    const addHint = noMax
      ? "Set Max schedule power in the integration's options first."
      : full
        ? "At most 6 schedules."
        : "";
    const pieces = barPieces(schedules, today)
      .map(
        (p) => `<button class="piece ${p.type}${p.enabled ? "" : " off"}${
          p.inUse ? " now" : ""
        }"
          data-edit="${escape(p.entityId)}" title="Edit"
          style="left:${(p.from / 1440) * 100}%;width:${
            ((p.to - p.from) / 1440) * 100
          }%"></button>`,
      )
      .join("");
    const rows = schedules
      .map(
        (s) => `<div class="row${s.enabled ? "" : " off"}${
          runsOn(s, today) ? "" : " later"
        }"
          data-edit="${escape(s.entityId)}">
          <span class="dot ${s.type}"></span>
          <div class="grow">${escape(s.title)}${
            s.inUse ? '<span class="now-label">now</span>' : ""
          }
            <div class="hint">${escape(s.power)} W · ${escape(
              daysText(s.days),
            )}</div></div>
          <button class="toggle${s.enabled ? " on" : ""}" data-toggle="${escape(
            s.entityId,
          )}"
            aria-label="${s.enabled ? "Switch off" : "Switch on"}"></button>
        </div>`,
      )
      .join("");
    return `<div class="head"><h2>Schedules</h2>
        <button class="add" data-add ${
          full || noMax ? "disabled" : ""
        }>+ Add</button></div>
      ${addHint ? `<div class="hint">${escape(addHint)}</div>` : ""}
      <div class="bar">${pieces}</div>
      <div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
      ${rows}`;
  }

  _onClick(event) {
    const target = event.target.closest("button, .row");
    if (!target) return;
    const data = target.dataset;
    if (data.toggle !== undefined) {
      event.stopPropagation();
      const on = target.classList.contains("on");
      this._hass.callService("switch", on ? "turn_off" : "turn_on", {
        entity_id: data.toggle,
      });
    } else if (data.add !== undefined) {
      this._open(null);
    } else if (data.edit !== undefined) {
      this._open(
        this._data.schedules.find((s) => s.entityId === data.edit) ?? null,
      );
    } else if (data.type !== undefined) {
      this._draft.type = data.type;
      this._renderDialog();
    } else if (data.daily !== undefined) {
      this._draft.days = "daily";
      this._renderDialog();
    } else if (data.day !== undefined) {
      const days = this._draft.days === "daily" ? [] : [...this._draft.days];
      const at = days.indexOf(data.day);
      if (at >= 0) days.splice(at, 1);
      else days.push(data.day);
      this._draft.days = days;
      this._renderDialog();
    } else if (data.action === "cancel") {
      this.shadowRoot.querySelector("dialog").close();
    } else if (data.action === "save") {
      this._save();
    } else if (data.action === "delete") {
      this._delete();
    }
  }

  _onChange(event) {
    const field = event.target.dataset?.field;
    if (!field || !this._draft) return;
    const value = event.target.value;
    this._draft[field] =
      field === "power" ? (value === "" ? NaN : Number(value)) : Number(value);
    // Never rebuild the form here: leaving the power box fires "change" on the
    // way to a tap on Save, and a rebuilt Save button would lose that tap.
    this._renderMessages();
  }

  _open(original) {
    this._original = original;
    this._draft = original
      ? {
          type: original.type,
          power: original.power,
          start: original.start,
          end: original.end,
          days: original.days === "daily" ? "daily" : [...original.days],
        }
      : {
          type: "charge",
          power: 1000,
          ...firstFreeWindow(this._data.schedules),
          days: "daily",
        };
    this._serverError = "";
    this._renderDialog();
    this.shadowRoot.querySelector("dialog").showModal();
  }

  _closed() {
    this._draft = null;
    this._original = null;
    this._serverError = "";
  }

  _errors() {
    const { schedules, maxPower } = this._data;
    return checkDraft(this._draft, schedules, {
      maxPower,
      original: this._original,
    });
  }

  _canSave(errors) {
    if (this._busy || Object.keys(errors).length) return false;
    return (
      this._original === null ||
      Object.keys(changedFields(this._original, this._draft)).length > 0
    );
  }

  _renderDialog() {
    const d = this._draft;
    if (!d) return;
    const { maxPower } = this._data;
    const select = (field) =>
      `<select data-field="${field}">${TIMES.map(
        (t) =>
          `<option value="${t}" ${t === d[field] ? "selected" : ""}>${toHm(
            t,
          )}</option>`,
      ).join("")}</select>`;
    const chips = DAYS.map(
      ([key, short]) =>
        `<button data-day="${key}" class="${
          d.days !== "daily" && d.days.includes(key) ? "sel" : ""
        }">${short}</button>`,
    ).join("");
    this.shadowRoot.querySelector("dialog").innerHTML = `
      <h3>${this._original ? "Edit schedule" : "New schedule"}</h3>
      <div class="error" data-msg="limit"></div><div class="error" data-msg="time"></div>
      <div class="field"><label>Type</label><div class="seg">
        <button data-type="charge" class="charge ${
          d.type === "charge" ? "sel" : ""
        }">Charge</button>
        <button data-type="use_battery" class="use_battery ${
          d.type === "use_battery" ? "sel" : ""
        }">Use battery</button>
      </div></div>
      <div class="field"><label>Power (W)</label>
        <input data-field="power" type="number" min="1" step="1" inputmode="numeric"
          value="${Number.isNaN(d.power) ? "" : escape(d.power)}">
        <div class="hint">${
          maxPower === null ? "" : `Up to ${escape(maxPower)} W`
        }</div>
        <div class="error" data-msg="power"></div></div>
      <div class="two">
        <div class="field"><label>Start</label>${select(
          "start",
        )}<div class="error" data-msg="start"></div></div>
        <div class="field"><label>End</label>${select(
          "end",
        )}<div class="error" data-msg="end"></div></div>
      </div>
      <div class="hint" data-hint="time"></div>
      <div class="field"><label>Days</label><div class="chips">
        <button data-daily class="${
          d.days === "daily" ? "sel" : ""
        }">Every day</button> ${chips}
      </div><div class="error" data-msg="days"></div></div>
      <div class="error" data-msg="server"></div>
      <div class="buttons">
        <span>${
          this._original
            ? '<button class="delete" data-action="delete">Delete</button>'
            : ""
        }</span>
        <span><button class="cancel" data-action="cancel">Cancel</button>
          <button class="save" data-action="save">Save</button></span>
      </div>`;
    this._renderMessages();
  }

  _renderMessages() {
    const dialog = this.shadowRoot.querySelector("dialog");
    const errors = this._errors();
    const d = this._draft;
    dialog.querySelector("[data-hint=time]").textContent =
      d.end < d.start
        ? "Runs over midnight."
        : "Times in 30-minute steps; an end before the start runs over midnight.";
    for (const box of dialog.querySelectorAll("[data-msg]")) {
      box.textContent =
        box.dataset.msg === "server"
          ? this._serverError
          : errors[box.dataset.msg] ?? "";
    }
    dialog.querySelector(".save").disabled = !this._canSave(errors);
  }

  async _call(service, data, target) {
    this._busy = true;
    this._serverError = "";
    this._renderMessages();
    try {
      await this._hass.callService(DOMAIN, service, data, target);
      this.shadowRoot.querySelector("dialog").close();
    } catch (error) {
      // Refused or not confirmed: keep the form and what was entered.
      this._serverError = error?.message ?? String(error);
    } finally {
      this._busy = false;
      if (this._draft) this._renderMessages();
    }
  }

  _save() {
    if (!this._canSave(this._errors())) return;
    if (this._original) {
      this._call(
        "update_schedule",
        changedFields(this._original, this._draft),
        {
          entity_id: this._original.entityId,
        },
      );
    } else {
      this._call("add_schedule", addData(this._draft, this._data.deviceId));
    }
  }

  _delete() {
    if (!this._original || !window.confirm(`Delete ${this._original.title}?`))
      return;
    this._call("delete_schedule", {}, { entity_id: this._original.entityId });
  }
}

class ScheduleCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    const options = candidates(this._hass)
      .map(
        (id) =>
          `<option value="${escape(id)}" ${
            id === this._config.entity ? "selected" : ""
          }>${escape(
            this._hass.states[id]?.attributes?.friendly_name ?? id,
          )}</option>`,
      )
      .join("");
    this.innerHTML = `<label>System (its Active schedule sensor)<br><select>${options}</select></label>`;
    this.querySelector("select").addEventListener("change", (event) => {
      this._config = { ...this._config, entity: event.target.value };
      this.dispatchEvent(
        new CustomEvent("config-changed", {
          detail: { config: this._config },
          bubbles: true,
          composed: true,
        }),
      );
    });
  }
}

if (!customElements.get(CARD)) customElements.define(CARD, ScheduleCard);
if (!customElements.get(EDITOR))
  customElements.define(EDITOR, ScheduleCardEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD)) {
  window.customCards.push({
    type: CARD,
    name: "EF PowerOcean schedules",
    description:
      "The EcoFlow app's scheduled tasks: see, add, change and delete them.",
  });
}
