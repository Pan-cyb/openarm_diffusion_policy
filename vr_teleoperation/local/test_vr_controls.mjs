// Offline browser/API doubles exercising the actual WebXR button handler.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";

const requests = [];
const callbacks = {};
const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, {
    hidden: false,
    addEventListener: (name, handler) => { callbacks[`${id}:${name}`] = handler; },
  });
  return elements.get(id);
};
const pose = { position: { x: 0, y: 0, z: 0 }, orientation: { x: 0, y: 0, z: 0, w: 1 } };
const source = (handedness) => ({
  handedness, targetRaySpace: {}, profiles: ["meta-quest-touch-plus"],
  gamepad: { buttons: Array.from({ length: 6 }, () => ({ pressed: false, value: 0 })),
    axes: [0, 0, 0, 0] },
});
const sources = [source("left"), source("right")];
let frameCallback;
let poll;
let status = { phase: "idle", ready: false, blocked: "left arm not ALIGNED", next_episode: 0 };
const session = {
  inputSources: sources, renderState: {},
  addEventListener: (name, handler) => { callbacks[`session:${name}`] = handler; },
  updateRenderState: () => {}, requestReferenceSpace: async () => ({}),
  requestAnimationFrame: (handler) => { frameCallback = handler; },
};
const renderer = () => ({
  attach() {}, render() {}, setCaptureStatus() {}, setLastAction() {},
  setStartProgress() {}, setNeutralProgress() {},
});
const context = vm.createContext({
  console, document: { getElementById: element,
    createElement: () => ({ getContext: () => ({}) }) },
  navigator: { xr: { isSessionSupported: async () => true,
    requestSession: async () => session } },
  XRWebGLLayer: function () {},
  setInterval: (fn) => { poll = fn; return 1; }, clearInterval() {},
  connect: async () => ({
    configuration: { view: "none", session: { mode: "immersive-ar" } },
    calibration: { enabled: false }, onCalibrationResult() {}, onClose() {},
    sendControl() {}, sendFrame() {}, close() {},
  }),
  createAxesRenderer: renderer, createSafetyControls: renderer,
  createInstructionPanel: renderer, createCameraPanel: renderer, createStereoPanel: renderer,
  fetch: async (path, options = {}) => {
    if (path === "/ui-version") return { json: async () => ({ build: "MOCK" }) };
    if (path === "/capture/status") return { ok: true, json: async () => ({ ...status }) };
    const payload = JSON.parse(options.body);
    requests.push({ path, ...payload });
    if (path === "/capture/command") {
      if (payload.command === "start") status = { ...status, phase: "starting", episode: 0 };
      if (payload.command === "success" || payload.command === "interrupt") {
        status = { ...status, phase: "closing" };
      }
    }
    return { ok: true, json: async () => ({ ...status }) };
  },
});
const code = (await fs.readFile(new URL("./static/ar.js", import.meta.url), "utf8"))
  .replace(/^import .*;$/gm, "");
vm.runInContext(code, context);
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
await flush();
callbacks["start:click"]();
await flush();
const frame = {
  getViewerPose: () => ({ transform: pose }),
  getPose: () => ({ transform: pose }),
};
const tick = async (time = 0) => { frameCallback(time, frame); await flush(); };
const button = (side, index, pressed) => { sources[side].gamepad.buttons[index].pressed = pressed; };

button(0, 4, true);
await tick();
assert.equal(requests.length, 0, "X before alignment must not start");
button(0, 4, false);
await tick();
button(1, 4, true);
await tick(1);
await tick(1601);
assert.deepEqual(requests.map((request) => request.command), ["start"]);
assert.equal(requests[0].path, "/arm-command", "A must only start arms");
button(1, 4, false);
await tick();
status = { ...status, ready: true, blocked: "" };
await poll();
button(0, 4, true);
await tick();
assert.equal(requests.at(-1).path, "/capture/command");
assert.equal(requests.at(-1).command, "start");
const count = requests.length;
await tick();
assert.equal(requests.length, count, "held X must not repeat");
button(0, 4, false);
await tick();
button(0, 4, true);
await tick();
assert.equal(requests.length, count, "X during Recorder startup must not finish");
button(0, 4, false);
await tick();
status = { ...status, phase: "recording" };
await poll();
button(0, 4, true);
await tick();
assert.equal(requests.at(-1).command, "success");
assert.equal(requests.at(-1).expected_phase, "recording");

status = { ...status, phase: "recording" };
await poll();
button(0, 4, false);
await tick();
button(0, 5, true);
await tick(0);
await tick(3000);
assert.equal(requests.filter((request) => request.path === "/neutral-capture").length, 0);
button(0, 4, true);
button(1, 4, true);
button(1, 5, true);
await tick(5000);
await tick(8000);
assert.deepEqual(requests.slice(-2).map((request) => request.command), ["stop", "interrupt"]);
assert.equal(requests.at(-2).path, "/arm-command", "B arm stop must be sent first");
assert.equal(requests.filter((request) => request.command === "success").length, 1);
callbacks["session:end"]();
await flush();
assert.equal(requests.at(-1).command, "interrupt");
console.log("PASS: X readiness/edge/ack gates, A non-recording, Y lock, B priority, session-end failure");
