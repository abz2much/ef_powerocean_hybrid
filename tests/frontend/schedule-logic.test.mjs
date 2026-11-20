// The schedule card's logic: the rules of the integration, mirrored.
// Run with: node --test tests/frontend/
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  addData,
  barPieces,
  changedFields,
  checkDraft,
  findSchedules,
  firstFreeWindow,
  overlaps,
  runsOn,
  toHm,
  toMinutes,
  weekdayIn,
} from "../../custom_components/ef_powerocean_hybrid/frontend/schedule-logic.js";

const SENSOR = "sensor.x_active_schedule";
const DOMAIN = "ef_powerocean_hybrid";

// The owner's six schedules (20 Nov 2026), all daily and on; 14 is in use.
const OWNER = [
  [13, "charge", "02:00", "06:00", 3000],
  [14, "charge", "06:00", "08:00", 1500],
  [15, "use_battery", "23:00", "02:00", 2000],
  [16, "use_battery", "08:00", "17:00", 5000],
  [17, "use_battery", "19:00", "23:00", 5000],
  [18, "use_battery", "17:00", "19:00", 5000],
];

function fakeHass({
  sensorState = "Charge 06:00-08:00 1500 W",
  maxPower = 6000,
} = {}) {
  const states = {
    [SENSOR]: {
      entity_id: SENSOR,
      state: sensorState,
      attributes: { max_schedule_power: maxPower },
    },
    // Another switch of the same device, without schedule attributes
    "switch.x_modbus_control": { state: "off", attributes: {} },
  };
  const entities = {
    [SENSOR]: {
      entity_id: SENSOR,
      device_id: "dev1",
      platform: DOMAIN,
      translation_key: "active_schedule",
    },
    "switch.x_modbus_control": {
      entity_id: "switch.x_modbus_control",
      device_id: "dev1",
      platform: DOMAIN,
      translation_key: "modbus_control",
    },
  };
  for (const [id, type, start, end, power] of OWNER) {
    // One renamed by the owner: found by its attributes, not its ID
    const entityId =
      id === 16 ? "switch.my_day_battery" : `switch.x_schedule_${id}`;
    states[entityId] = {
      entity_id: entityId,
      state: "on",
      attributes: {
        id,
        type,
        start,
        end,
        power,
        days: "daily",
        in_use: id === 14,
      },
    };
    entities[entityId] = {
      entity_id: entityId,
      device_id: "dev1",
      platform: DOMAIN,
    };
  }
  return { states, entities, config: { time_zone: "Europe/Dublin" } };
}

function schedules(hass = fakeHass()) {
  return findSchedules(hass, SENSOR).schedules;
}

function byId(id) {
  return schedules().find((s) => s.id === id);
}

test("converts times both ways", () => {
  assert.equal(toMinutes("06:30"), 390);
  assert.equal(toMinutes("06:30:00"), 390);
  assert.equal(toHm(390), "06:30");
  assert.equal(toHm(0), "00:00");
});

test("finds the schedules by their attributes, sorted by start", () => {
  const found = findSchedules(fakeHass(), SENSOR);

  assert.equal(found.deviceId, "dev1");
  assert.equal(found.maxPower, 6000);
  assert.equal(found.available, true);
  assert.deepEqual(
    found.schedules.map((s) => s.id),
    [13, 14, 16, 18, 17, 15],
  );
  assert.equal(byId(16).entityId, "switch.my_day_battery");
  assert.equal(byId(14).title, "Charge 06:00–08:00");
  assert.equal(byId(15).title, "Use battery 23:00–02:00");
  assert.deepEqual(
    { start: byId(15).start, end: byId(15).end, inUse: byId(14).inUse },
    { start: 1380, end: 120, inUse: true },
  );
});

test("is not available while the switches have no schedule attributes", () => {
  const hass = fakeHass();
  for (const state of Object.values(hass.states)) {
    if (state.entity_id?.startsWith("switch.")) state.attributes = {};
  }

  assert.equal(findSchedules(hass, SENSOR).available, false);
});

test("is not available while the sensor is unavailable", () => {
  assert.equal(
    findSchedules(fakeHass({ sensorState: "unavailable" }), SENSOR).available,
    false,
  );
});

test("is not available without the sensor", () => {
  assert.equal(findSchedules(fakeHass(), "sensor.gone").available, false);
});

test("reads a missing max schedule power as null", () => {
  assert.equal(
    findSchedules(fakeHass({ maxPower: null }), SENSOR).maxPower,
    null,
  );
});

test("splits an over-midnight schedule in two bar pieces", () => {
  const pieces = barPieces([byId(15)], "fri");

  assert.deepEqual(
    pieces.map((p) => [p.from, p.to]),
    [
      [1380, 1440],
      [0, 120],
    ],
  );
});

test("leaves schedules that do not run today off the bar", () => {
  const monday = { ...byId(13), days: ["mon"] };

  assert.equal(runsOn(monday, "tue"), false);
  assert.equal(runsOn(monday, "mon"), true);
  assert.deepEqual(barPieces([monday], "tue"), []);
  assert.equal(barPieces([monday], "mon").length, 1);
});

test("uses Home Assistant's time zone for today", () => {
  const now = new Date("2026-11-20T12:30:00Z");

  assert.equal(weekdayIn("Pacific/Auckland", now), "sat");
  assert.equal(weekdayIn("UTC", now), "fri");
});

test("overlaps as the integration: back to back and over midnight", () => {
  const hm = toMinutes;
  assert.equal(
    overlaps(hm("02:00"), hm("06:00"), hm("06:00"), hm("08:00")),
    false,
  );
  assert.equal(
    overlaps(hm("23:00"), hm("02:00"), hm("01:00"), hm("03:00")),
    true,
  );
  assert.equal(
    overlaps(hm("23:00"), hm("02:00"), hm("02:00"), hm("04:00")),
    false,
  );
  assert.equal(
    overlaps(hm("08:00"), hm("17:00"), hm("16:30"), hm("18:00")),
    true,
  );
});

function draftOf(schedule, changes = {}) {
  const { type, power, start, end, days } = schedule;
  return { type, power, start, end, days, ...changes };
}

const NEW = {
  type: "charge",
  power: 1000,
  start: 1260,
  end: 1320,
  days: "daily",
};

test("refuses times off the 30-minute steps", () => {
  const errors = checkDraft({ ...NEW, start: 375 }, [], {
    maxPower: 6000,
    original: null,
  });
  assert.equal(errors.start, "Use 30-minute steps from 00:00 to 23:30.");
});

test("refuses the same start and end", () => {
  const errors = checkDraft({ ...NEW, end: NEW.start }, [], {
    maxPower: 6000,
    original: null,
  });
  assert.equal(errors.end, "Start and end must differ.");
});

test("refuses a power outside 1 to the max", () => {
  for (const power of [0, 6001, 1.5]) {
    const errors = checkDraft({ ...NEW, power }, [], {
      maxPower: 6000,
      original: null,
    });
    assert.equal(
      errors.power,
      "Enter whole watts from 1 to 6000 W.",
      String(power),
    );
  }
});

test("asks for the max schedule power option first", () => {
  const errors = checkDraft(NEW, [], { maxPower: null, original: null });
  assert.equal(
    errors.power,
    "Set Max schedule power in the integration's options first.",
  );
});

test("refuses chosen days without a day", () => {
  const errors = checkDraft({ ...NEW, days: [] }, [], {
    maxPower: 6000,
    original: null,
  });
  assert.equal(errors.days, "Choose at least one day.");
});

test("refuses a new schedule that overlaps", () => {
  const errors = checkDraft({ ...NEW, start: 420, end: 540 }, schedules(), {
    maxPower: 6000,
    original: null,
  });
  assert.equal(errors.time, "Overlaps Charge 06:00–08:00.");
});

test("an edit does not overlap itself", () => {
  const original = byId(14);
  const errors = checkDraft(draftOf(original, { end: 540 }), schedules(), {
    maxPower: 6000,
    original,
  });
  assert.equal(errors.time, "Overlaps Use battery 08:00–17:00.");
});

test("an unchanged power above the max is not checked", () => {
  const original = { ...byId(14), power: 7000 };
  const errors = checkDraft(draftOf(original, { days: ["mon"] }), schedules(), {
    maxPower: 6000,
    original,
  });
  assert.deepEqual(errors, {});
});

test("allows at most 6 schedules", () => {
  const errors = checkDraft(NEW, schedules(), {
    maxPower: 6000,
    original: null,
  });
  assert.equal(errors.limit, "At most 6 schedules.");
});

test("an unchanged edit sends nothing", () => {
  assert.deepEqual(changedFields(byId(14), draftOf(byId(14))), {});
});

test("an edit sends only what changed", () => {
  assert.deepEqual(changedFields(byId(14), draftOf(byId(14), { end: 540 })), {
    end: "09:00:00",
  });
});

test("chosen days back to every day sends daily", () => {
  const original = { ...byId(14), days: ["mon", "tue"] };
  assert.deepEqual(
    changedFields(original, draftOf(original, { days: "daily" })),
    {
      daily: true,
    },
  );
});

test("the data to add a daily schedule", () => {
  assert.deepEqual(addData(NEW, "dev1"), {
    device_id: ["dev1"],
    type: "charge",
    power: 1000,
    start: "21:00:00",
    end: "22:00:00",
  });
});

test("the data to add a schedule on chosen days", () => {
  assert.deepEqual(addData({ ...NEW, days: ["sat", "sun"] }, "dev1").days, [
    "sat",
    "sun",
  ]);
});

test("a new schedule starts in the first free hour", () => {
  const some = schedules().filter((s) => [13, 14, 15].includes(s.id));
  // 23:00-02:00, 02:00-06:00 and 06:00-08:00 are taken
  assert.deepEqual(firstFreeWindow(some), { start: 480, end: 540 });
});

test("a new schedule starts at 00:00 when no hour is free", () => {
  assert.deepEqual(firstFreeWindow(schedules()), { start: 0, end: 60 });
});
