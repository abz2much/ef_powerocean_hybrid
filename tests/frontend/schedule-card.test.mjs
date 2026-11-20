// The schedule card registers itself with Home Assistant's frontend.
// Run with: node --test tests/frontend/
import { test } from "node:test";
import assert from "node:assert/strict";

const defined = {};
globalThis.HTMLElement = class {};
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

test("defines the card and its editor", () => {
  assert.ok(defined["ef-powerocean-schedule-card"]);
  assert.ok(defined["ef-powerocean-schedule-card-editor"]);
});

test("appears in the card picker", () => {
  assert.deepEqual(window.customCards, [
    {
      type: "ef-powerocean-schedule-card",
      name: "EF PowerOcean schedules",
      description:
        "The EcoFlow app's scheduled tasks: see, add, change and delete them.",
    },
  ]);
});

test("picks the Active schedule sensor of the integration", () => {
  const hass = {
    entities: {
      "sensor.other": {
        entity_id: "sensor.other",
        platform: "other",
        translation_key: "active_schedule",
      },
      "sensor.x_house_power": {
        entity_id: "sensor.x_house_power",
        platform: "ef_powerocean_hybrid",
        translation_key: "house_power",
      },
      "sensor.x_active_schedule": {
        entity_id: "sensor.x_active_schedule",
        platform: "ef_powerocean_hybrid",
        translation_key: "active_schedule",
      },
    },
  };

  assert.deepEqual(defined["ef-powerocean-schedule-card"].getStubConfig(hass), {
    entity: "sensor.x_active_schedule",
  });
});
