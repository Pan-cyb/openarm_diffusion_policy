// Copyright 2026 Enactic, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { connect } from "./connection.js";
import { createAxesRenderer } from "./axes.js";
import { createInstructionPanel } from "./instructions.js";
import { createCameraPanel } from "./panel.js";
import { createSafetyControls } from "./safety-controls.js";
import { createStereoPanel } from "./stereo.js";

// Used only when the node's view configuration cannot be read.
const FALLBACK_CONFIGURATION = {
  view: "mono",
  session: { mode: "immersive-ar" },
  panel: { lock: "room", distance: 1.3, width: 1.5 },
};

function hidePreparing() {
  const overlay = document.getElementById("preparing");
  if (overlay) {
    overlay.hidden = true;
  }
}

fetch("/ui-version", { cache: "no-store" })
  .then((response) => response.json())
  .then((version) => {
    document.getElementById("ui-version").textContent = `OpenArm UI ${version.build}`;
  })
  .catch(() => {
    document.getElementById("ui-version").textContent = "UI version unavailable: reload";
  });

if (navigator.xr) {
  let connection = null;
  let runningSession = null;
  let configuration = null;
  let cameraPanel = null;
  let instructionPanel = null;
  let axesRenderer = null;
  let safetyControls = null;
  let buttonAStartedAt = null;
  let buttonYStartedAt = null;
  let startCommandSent = false;
  let neutralCaptureSent = false;
  let previousButtonB = false;
  let previousButtonX = false;
  let teleopPaused = false;
  let captureStatus = null;
  let capturePending = false;
  let statusTimer = null;
  let statusPending = false;
  let armRequestGeneration = 0;

  const START_HOLD_MS = 1500;
  const NEUTRAL_HOLD_MS = 2000;

  // The node pushes its configuration as soon as the connection opens,
  // so this page needs no route of its own to read it: whoever serves
  // this file, the node still decides how it draws itself. The session
  // can only start once this has resolved, so the view and the panels
  // are always set by then. The preparing overlay stays up until ICE
  // gathering has finished, which is the wait before that offer is
  // posted.
  const sessionReady = connect({ onGathered: hidePreparing }).then((opened) => {
    connection = opened;
    configuration = opened.configuration || FALLBACK_CONFIGURATION;

    // How many images: the default "mono" draws one, "stereo" draws one
    // per eye, and "none" shows no camera at all. Where they hang is a
    // separate question, answered by "panel: lock" in the configuration.
    if (configuration.view === "stereo") {
      cameraPanel = createStereoPanel(configuration, opened.tracks);
    } else if (configuration.view !== "none") {
      cameraPanel = createCameraPanel(configuration, opened.tracks.right);
    }

    // Only a node started with --calibration wants the instructions, and
    // only it acts on the Y button, so the panel that asks for a head
    // turn is never in front of an operator whose Y button means
    // something else. Read after the view, since the panel has to know
    // whether a camera view is drawing the frame or it is the only one
    // there.
    if (opened.calibration.enabled) {
      instructionPanel = createInstructionPanel({
        clears: cameraPanel === null,
      });
    }

    // The node only speaks to say what came of a calibration run.
    opened.onCalibrationResult((message) => {
      if (instructionPanel) {
        instructionPanel.setResult(message);
      }
    });

    // Losing the connection ends the session: an operator left inside a
    // frozen view has no way to tell that nothing is reaching the robot.
    opened.onClose(() => {
      connection = null;
      if (runningSession) {
        runningSession.end();
        runningSession = null;
      }
    });

    return configuration;
  });

  function sendControl(message) {
    if (connection) {
      connection.sendControl(message);
    }
  }

  async function postArmCommand(command, label) {
    const generation = ++armRequestGeneration;
    if (safetyControls) {
      safetyControls.setLastAction(`${label} requested`);
    }
    try {
      const response = await fetch("/arm-command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      if (safetyControls) {
        safetyControls.setLastAction(label);
      }
      if (command === "start" && generation === armRequestGeneration) {
        teleopPaused = false;
      }
    } catch (error) {
      console.error(`cannot send ${command}: ${error}`);
      if (safetyControls) {
        safetyControls.setLastAction(`${label} FAILED`);
      }
    }
  }

  function showCaptureStatus(status) {
    captureStatus = status;
    safetyControls?.setCaptureStatus(status);
  }

  async function refreshCaptureStatus() {
    if (statusPending) return;
    statusPending = true;
    try {
      const response = await fetch("/capture/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      showCaptureStatus(await response.json());
    } catch (error) {
      showCaptureStatus({ phase: "unknown", reason: "Capture status unavailable" });
    } finally {
      statusPending = false;
    }
  }

  async function postCaptureCommand(command) {
    if (command === "interrupt" && captureStatus?.phase === "disabled") return;
    if (capturePending && command !== "interrupt") return;
    const previous = captureStatus;
    if (command !== "interrupt") capturePending = true;
    try {
      const response = await fetch("/capture/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          command,
          expected_phase: previous?.phase,
          expected_episode: previous?.phase === "idle" ? previous.next_episode : previous?.episode,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      showCaptureStatus(result);
    } catch (error) {
      safetyControls?.setLastAction(`CAPTURE: ${error.message}`);
    } finally {
      if (command !== "interrupt") capturePending = false;
      void refreshCaptureStatus();
    }
  }

  function neutralSnapshot(response) {
    const required = ["pose_reference", "pose_right", "pose_left"];
    if (required.some((name) => !response[name])) {
      return null;
    }
    return {
      pose_reference: { ...response.pose_reference },
      pose_right: { ...response.pose_right },
      pose_left: { ...response.pose_left },
    };
  }

  async function captureHomeNeutral(frame) {
    try {
      const response = await fetch("/neutral-capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ frame }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || `HTTP ${response.status}`);
      }
      sendControl({ type: "session-start" });
      const offset = result.frame_offset
        .map((value) => Number(value).toFixed(3))
        .join(", ");
      safetyControls.setLastAction(`NEUTRAL [${offset}] — HOLD A`);
    } catch (error) {
      console.error(`neutral capture failed: ${error}`);
      safetyControls.setLastAction("NEUTRAL FAILED — B to stop");
    }
  }

  function handleSafetyButtons(response, time) {
    const buttonA = response.button_a === true;
    const buttonB = response.button_b === true;
    const buttonY = response.button_y === true;
    const buttonX = response.button_x === true;

    if (buttonB && !previousButtonB) {
      teleopPaused = true;
      void postArmCommand("stop", "STOP");
      void postCaptureCommand("interrupt");
    }
    previousButtonB = buttonB;
    const pressedX = buttonX && !previousButtonX;
    previousButtonX = buttonX;
    if (buttonB) {
      buttonAStartedAt = null;
      buttonYStartedAt = null;
      safetyControls?.setStartProgress(0);
      safetyControls?.setNeutralProgress(0);
      return;
    }
    if (pressedX) {
      if (captureStatus?.phase === "idle" && captureStatus.ready) {
        void postCaptureCommand("start");
      } else if (captureStatus?.phase === "recording") {
        void postCaptureCommand("success");
      } else {
        safetyControls?.setLastAction(captureStatus?.blocked || "X: WAIT FOR READY / RECORDER");
      }
    }
    if (["starting", "recording", "closing"].includes(captureStatus?.phase)) {
      buttonAStartedAt = null;
      buttonYStartedAt = null;
      safetyControls?.setStartProgress(0);
      safetyControls?.setNeutralProgress(0);
      return;
    }

    if (buttonY) {
      if (buttonYStartedAt === null) {
        buttonYStartedAt = time;
        neutralCaptureSent = false;
      }
      const progress = Math.min(1, (time - buttonYStartedAt) / NEUTRAL_HOLD_MS);
      if (safetyControls) {
        safetyControls.setNeutralProgress(progress);
      }
      if (progress >= 1 && !neutralCaptureSent) {
        neutralCaptureSent = true;
        const snapshot = neutralSnapshot(response);
        if (!snapshot) {
          safetyControls.setLastAction("NEUTRAL NEEDS BOTH HANDS");
        } else {
          teleopPaused = true;
          safetyControls.setLastAction("CAPTURING HOME NEUTRAL");
          void captureHomeNeutral(snapshot);
        }
      }
    } else {
      buttonYStartedAt = null;
      neutralCaptureSent = false;
      if (safetyControls) {
        safetyControls.setNeutralProgress(0);
      }
    }

    if (buttonA) {
      if (buttonAStartedAt === null) {
        buttonAStartedAt = time;
        startCommandSent = false;
      }
      const progress = Math.min(1, (time - buttonAStartedAt) / START_HOLD_MS);
      if (safetyControls) {
        safetyControls.setStartProgress(progress);
      }
      if (progress >= 1 && !startCommandSent) {
        startCommandSent = true;
        void postArmCommand("start", "ARM START");
      }
    } else {
      buttonAStartedAt = null;
      startCommandSent = false;
      if (safetyControls) {
        safetyControls.setStartProgress(0);
      }
    }
  }

  function log(message) {
    // document.getElementById("log").innerText += `${message}\n`;
    // sendControl({type: "log", message: `${message}`});
  }
  function onSessionEnd(event) {
    log("ended");
    clearInterval(statusTimer);
    statusTimer = null;
    void postCaptureCommand("interrupt");
    if (cameraPanel) {
      cameraPanel.close();
    }
    runningSession = null;
    if (connection) {
      connection.close();
      connection = null;
    }
  }
  function onSelectStart(event) {
    const response = {
      type: "select-start",
      buttons: event.inputSource.gamepad.buttons,
      axes: event.inputSource.gamepad.axes,
    };
    sendControl(response);
  }
  function onSelect(event) {
    const response = {
      type: "select",
      buttons: event.inputSource.gamepad.buttons,
      axes: event.inputSource.gamepad.axes,
    };
    sendControl(response);
  }
  function onSelectEnd(event) {
    const response = {
      type: "select-end",
      buttons: event.inputSource.gamepad.buttons,
      axes: event.inputSource.gamepad.axes,
    };
    sendControl(response);
  }
  function onSqueezeStart(event) {
    const response = {
      type: "squeeze-start",
      buttons: event.inputSource.gamepad.buttons,
      axes: event.inputSource.gamepad.axes,
    };
    sendControl(response);
  }
  function onSqueeze(event) {
    const response = {
      type: "squeeze",
      buttons: event.inputSource.gamepad.buttons,
      axes: event.inputSource.gamepad.axes,
    };
    sendControl(response);
  }
  function onSqueezeEnd(event) {
    const response = {
      type: "squeeze-end",
      buttons: event.inputSource.gamepad.buttons,
      axes: event.inputSource.gamepad.axes,
    };
    sendControl(response);
  }
  function sendFrameResponse(response) {
    if (instructionPanel) {
      // An absent button is a released one, which is how the node reads
      // it too, so a controller that falls asleep or goes missing ends
      // the run on the panel just as it ends it there.
      instructionPanel.setPressed(response.button_y === true);
    }
    // Frames go on the unreliable channel, which numbers them: only the
    // newest pose is worth anything, so a lost one is dropped rather
    // than retransmitted behind the frames that follow it.
    if (connection) {
      connection.sendFrame(response);
    }
  }

  function readSafetyButtons(session, response) {
    for (const source of session.inputSources) {
      const gamepad = source.gamepad;
      if (!gamepad) {
        continue;
      }
      if (source.handedness === "right") {
        const a = gamepad.buttons[4];
        const b = gamepad.buttons[5];
        if (a) {
          response.button_a = a.pressed;
        }
        if (b) {
          response.button_b = b.pressed;
        }
      } else if (source.handedness === "left") {
        const x = gamepad.buttons[4];
        const y = gamepad.buttons[5];
        if (x) {
          response.button_x = x.pressed;
        }
        if (y) {
          response.button_y = y.pressed;
        }
      }
    }
  }

  function sendFrame(session, space, time, frame) {
    const response = {
      type: "frame",
      time: time,
    };
    // Read before the controller check below, so a consumer driving something
    // from head motion keeps tracking while the controllers are off or asleep.
    // The hand poses are made relative to this pose by the node, so when it is
    // missing the node drops them and only the head keeps flowing.
    const viewerPose = frame.getViewerPose(space);
    if (viewerPose) {
      const transform = viewerPose.transform;
      response.pose_reference = {
        x: transform.position.x,
        y: transform.position.y,
        z: transform.position.z,
        qx: transform.orientation.x,
        qy: transform.orientation.y,
        qz: transform.orientation.z,
        qw: transform.orientation.w,
      };
    }
    readSafetyButtons(session, response);
    if (session.inputSources.length < 2) {
      handleSafetyButtons(response, time);
      sendFrameResponse(response);
      return;
    }
    for (const source of session.inputSources) {
      if (source.handedness === "none") {
        continue;
      }
      const suffix = `_${source.handedness}`;
      // The target ray space is the OpenXR aim pose: its -Z points where
      // the controller points. The node maps the gripper onto that axis
      // directly, so it must not be the grip pose, whose -Z runs along the
      // handle and would turn the handle's tilt into the gripper's.
      const pose = frame.getPose(source.targetRaySpace, space);
      if (pose) {
        response[`pose${suffix}`] = {
          x: pose.transform.position.x,
          y: pose.transform.position.y,
          z: pose.transform.position.z,
          qx: pose.transform.orientation.x,
          qy: pose.transform.orientation.y,
          qz: pose.transform.orientation.z,
          qw: pose.transform.orientation.w,
        };
      }
      const gamepad = source.gamepad;
      if (gamepad) {
        if (
          source.profiles.includes("pico-4u") ||
          source.profiles.includes("meta-quest-touch-plus") ||
          source.profiles.includes("oculus-touch-v3")
        ) {
          const trigger = gamepad.buttons[0];
          response[`trigger${suffix}`] = trigger.value;

          // The squeeze sits next to the trigger under the xr-standard
          // mapping. Sent as its 0..1 value rather than a pressed flag, so
          // that a consumer can pick its own threshold.
          const grip = gamepad.buttons[1];
          if (grip) {
            response[`grip${suffix}`] = grip.value;
          }
          if (source.handedness === "right") {
            const a = gamepad.buttons[4];
            const b = gamepad.buttons[5];
            response.button_a = a.pressed;
            response.button_b = b.pressed;
          } else {
            const x = gamepad.buttons[4];
            const y = gamepad.buttons[5];
            response.button_x = x.pressed;
            response.button_y = y.pressed;
          }
        }
        // Send the whole array and let the node pick the pair it wants.
        // Testing the axes for truthiness, as this once did, meant the
        // joystick was never sent from a Quest at all: its Touch controllers
        // have no touchpad and report axes[0..1] as a constant 0, so the
        // check could never pass. A centred thumbstick reads exactly 0 too,
        // which would have gated out the resting position even on a device
        // that does have a touchpad.
        if (gamepad.axes.length >= 2) {
          response[`joystick${suffix}`] = Array.from(gamepad.axes);
        }
      }
    }
    handleSafetyButtons(response, time);
    if (teleopPaused) {
      delete response.pose_right;
      delete response.pose_left;
      delete response.trigger_right;
      delete response.trigger_left;
      delete response.grip_right;
      delete response.grip_left;
    }
    sendFrameResponse(response);
  }
  function onSessionStart(session) {
    runningSession = session;
    // TODO: Add device information
    const response = {
      type: "session-start",
    };
    sendControl(response);

    session.addEventListener("end", onSessionEnd);

    session.addEventListener("selectstart", onSelectStart);
    session.addEventListener("select", onSelect);
    session.addEventListener("selectend", onSelectEnd);

    session.addEventListener("squeezestart", onSqueezeStart);
    session.addEventListener("squeeze", onSqueeze);
    session.addEventListener("squeezeend", onSqueezeEnd);

    // The render state is needed to use immersive AR even when nothing
    // is drawn into it. The camera views draw their panel into it.
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl", { xrCompatible: true });
    session.updateRenderState({ baseLayer: new XRWebGLLayer(session, gl) });
    axesRenderer = createAxesRenderer();
    axesRenderer.attach(gl);
    safetyControls = createSafetyControls();
    safetyControls.attach(gl);
    captureStatus = null;
    previousButtonB = false;
    previousButtonX = false;
    void refreshCaptureStatus();
    statusTimer = setInterval(refreshCaptureStatus, 500);
    if (cameraPanel) {
      cameraPanel.attach(gl);
    }
    if (instructionPanel) {
      instructionPanel.attach(gl);
    }

    Promise.all([
      // The hand poses and the head pose are both read in the
      // world-fixed local space, and the node makes the hand positions
      // relative to the head pose without inheriting the head rotation.
      // The panel hangs in whichever space "panel: lock" names: the
      // room is the local space, the head is the viewer space.
      session.requestReferenceSpace("viewer"),
      session.requestReferenceSpace("local"),
    ])
      .then(([viewerSpace, localSpace]) => {
        const panel = configuration.panel || {};
        const panelSpace = panel.lock === "head" ? viewerSpace : localSpace;
        function onFrame(time, frame) {
          log("sources: " + session.inputSources.length);
          sendFrame(session, localSpace, time, frame);
          if (cameraPanel) {
            cameraPanel.render(session, panelSpace, frame);
          }
          // After the camera view, so the text is over the image rather
          // than cleared away with the rest of the frame, and always in
          // the viewer space so it stays readable through a head turn.
          if (instructionPanel) {
            instructionPanel.render(session, viewerSpace, frame);
          }
          axesRenderer.render(
            session,
            localSpace,
            frame,
            cameraPanel === null && instructionPanel === null,
          );
          safetyControls.render(session, viewerSpace, frame);
          session.requestAnimationFrame(onFrame);
        }
        session.requestAnimationFrame(onFrame);
      })
      .catch((error) => {
        alert(error);
      });
  }
  function onStart(mode) {
    navigator.xr.requestSession(mode).then(onSessionStart);
  }

  // The session mode is configured with the head camera view so that
  // passthrough can be turned off without changing this file.
  sessionReady
    .then((configuration) => {
      const mode = configuration.session.mode;
      navigator.xr.isSessionSupported(mode).then((isSupported) => {
        if (isSupported) {
          // WebXR requires explicit user interaction on start. We use
          // button click here.
          document.getElementById("start").addEventListener("click", () => {
            onStart(mode);
          });
        }
      });
    })
    .catch((error) => {
      hidePreparing();
      console.error("cannot connect to the node: " + error);
    });
} else {
  hidePreparing();
}
