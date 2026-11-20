// The schedule card's logic, without the page: finding the schedules, the
// day bar, and the rules of the integration (cloud/schedule_rules.py,
// cloud/schedules.py), mirrored so the form can tell at once what the
// integration would refuse. The integration still checks everything.

const DOMAIN = "ef_powerocean_hybrid";
const DAY_MINUTES = 1440;
const STEP = 30;
const LAST_MINUTE = 23 * 60 + 30;
const MAX_SCHEDULES = 6;
const TITLES = { charge: "Charge", use_battery: "Use battery" };
const WEEKDAYS = {
  Mon: "mon",
  Tue: "tue",
  Wed: "wed",
  Thu: "thu",
  Fri: "fri",
  Sat: "sat",
  Sun: "sun",
};

export function toMinutes(text) {
  const [hours, minutes] = text.split(":").map(Number);
  return hours * 60 + minutes;
}

export function toHm(minutes) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
}

function title(type, start, end) {
  return `${TITLES[type] ?? type} ${toHm(start)}–${toHm(end)}`;
}

function isSchedule(attributes) {
  return ["id", "type", "start", "end"].every(
    (key) => key in (attributes ?? {}),
  );
}

// The schedules of the system whose Active schedule sensor is sensorId: the
// switches of the same device that carry a schedule's attributes.
export function findSchedules(hass, sensorId) {
  const sensor = hass.states[sensorId];
  const deviceId = hass.entities[sensorId]?.device_id ?? null;
  const maxPower = sensor?.attributes?.max_schedule_power ?? null;
  const schedules = Object.values(hass.entities)
    .filter(
      (entry) =>
        entry.platform === DOMAIN &&
        entry.device_id === deviceId &&
        entry.entity_id.startsWith("switch.") &&
        isSchedule(hass.states[entry.entity_id]?.attributes),
    )
    .map((entry) => {
      const state = hass.states[entry.entity_id];
      const a = state.attributes;
      const start = toMinutes(a.start);
      const end = toMinutes(a.end);
      return {
        entityId: entry.entity_id,
        id: a.id,
        type: a.type,
        start,
        end,
        power: a.power,
        days: a.days,
        enabled: state.state === "on",
        inUse: Boolean(a.in_use),
        title: title(a.type, start, end),
      };
    })
    .sort((a, b) => a.start - b.start);
  // The sensor is available only with MQTT and once the list was read; an
  // empty list is still a list, to which the card can add.
  const available =
    sensor !== undefined &&
    !["unavailable", "unknown"].includes(sensor.state) &&
    deviceId !== null;
  return { deviceId, maxPower, available, schedules };
}

// What the card shows, as one value: the card redraws only when it changes,
// not on every update of Home Assistant (which would drop taps).
export function viewKey(hass, sensorId, weekday) {
  const { available, maxPower, schedules } = findSchedules(hass, sensorId);
  return JSON.stringify([
    available,
    maxPower,
    weekday,
    hass.states[sensorId]?.state,
    schedules,
  ]);
}

export function weekdayIn(timeZone, now) {
  const short = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "short",
  }).format(now);
  return WEEKDAYS[short];
}

export function runsOn(schedule, weekday) {
  return schedule.days === "daily" || schedule.days.includes(weekday);
}

// The pieces of the day bar, in minutes: the schedules that run on the day
// (by their start day); one over midnight is two pieces.
export function barPieces(schedules, weekday) {
  return schedules
    .filter((schedule) => runsOn(schedule, weekday))
    .flatMap(({ entityId, type, start, end, enabled, inUse }) => {
      const piece = { entityId, type, enabled, inUse };
      return start < end
        ? [{ ...piece, from: start, to: end }]
        : [
            { ...piece, from: start, to: DAY_MINUTES },
            { ...piece, from: 0, to: end },
          ].filter((p) => p.from < p.to);
    });
}

function segments(start, end) {
  return start < end
    ? [[start, end]]
    : [
        [start, DAY_MINUTES],
        [0, end],
      ];
}

// As windows_overlap: two daily windows share a minute; back to back is fine.
export function overlaps(start1, end1, start2, end2) {
  return segments(start1, end1).some(([a1, b1]) =>
    segments(start2, end2).some(([a2, b2]) => a1 < b2 && a2 < b1),
  );
}

// Where a new schedule starts: the first free hour, else 00:00-01:00.
export function firstFreeWindow(schedules) {
  for (let start = 0; start <= LAST_MINUTE; start += STEP) {
    const end = (start + 60) % DAY_MINUTES;
    if (!schedules.some((s) => overlaps(start, end, s.start, s.end)))
      return { start, end };
  }
  return { start: 0, end: 60 };
}

function onStep(minutes) {
  return (
    Number.isInteger(minutes) &&
    minutes >= 0 &&
    minutes <= LAST_MINUTE &&
    minutes % STEP === 0
  );
}

function sameDays(a, b) {
  if (a === "daily" || b === "daily") return a === b;
  return a.length === b.length && a.every((day) => b.includes(day));
}

// What the integration would refuse, by field. As update_schedule, an edit
// checks the power only when it changed and the overlap only when the time
// changed.
export function checkDraft(draft, schedules, { maxPower, original }) {
  const errors = {};
  const isNew = original === null;
  if (maxPower === null || maxPower === undefined) {
    errors.power = "Set Max schedule power in the integration's options first.";
  } else if (
    (isNew || draft.power !== original.power) &&
    !(
      Number.isInteger(draft.power) &&
      draft.power >= 1 &&
      draft.power <= maxPower
    )
  ) {
    errors.power = `Enter whole watts from 1 to ${maxPower} W.`;
  }
  if (!onStep(draft.start))
    errors.start = "Use 30-minute steps from 00:00 to 23:30.";
  if (!onStep(draft.end))
    errors.end = "Use 30-minute steps from 00:00 to 23:30.";
  else if (draft.start === draft.end) errors.end = "Start and end must differ.";
  if (draft.days !== "daily" && draft.days.length === 0)
    errors.days = "Choose at least one day.";
  const timeChanged =
    isNew || draft.start !== original.start || draft.end !== original.end;
  if (timeChanged && !errors.start && !errors.end) {
    const other = schedules.find(
      (s) =>
        (isNew || s.entityId !== original.entityId) &&
        overlaps(draft.start, draft.end, s.start, s.end),
    );
    if (other) errors.time = `Overlaps ${other.title}.`;
  }
  if (isNew && schedules.length >= MAX_SCHEDULES)
    errors.limit = "At most 6 schedules.";
  return errors;
}

function time(minutes) {
  return `${toHm(minutes)}:00`;
}

// The data of update_schedule: only what changed.
export function changedFields(original, draft) {
  const data = {};
  if (draft.type !== original.type) data.type = draft.type;
  if (draft.power !== original.power) data.power = draft.power;
  if (draft.start !== original.start) data.start = time(draft.start);
  if (draft.end !== original.end) data.end = time(draft.end);
  if (!sameDays(draft.days, original.days)) {
    if (draft.days === "daily") data.daily = true;
    else data.days = [...draft.days];
  }
  return data;
}

// The data of add_schedule; no days for every day.
export function addData(draft, deviceId) {
  const data = {
    device_id: [deviceId],
    type: draft.type,
    power: draft.power,
    start: time(draft.start),
    end: time(draft.end),
  };
  if (draft.days !== "daily") data.days = [...draft.days];
  return data;
}
