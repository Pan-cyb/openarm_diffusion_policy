// Copyright 2026
//
// Head-locked safety control reminder for the real OpenArm WebXR dataflow.

const VERTEX_SHADER = `
attribute vec2 a_corner;
uniform mat4 u_projection;
uniform mat4 u_view;
uniform vec2 u_half_extent;
uniform vec2 u_center;
uniform float u_distance;
varying vec2 v_uv;
void main() {
  gl_Position = u_projection * u_view *
    vec4(a_corner * u_half_extent + u_center, -u_distance, 1.0);
  v_uv = vec2((a_corner.x + 1.0) * 0.5, (1.0 - a_corner.y) * 0.5);
}
`;

const FRAGMENT_SHADER = `
precision mediump float;
uniform sampler2D u_texture;
varying vec2 v_uv;
void main() {
  gl_FragColor = texture2D(u_texture, v_uv);
}
`;

const CANVAS = { width: 960, height: 300 };
const PANEL = { distance: 1.1, width: 0.48, center: -0.4 };

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader));
  }
  return shader;
}

class SafetyControls {
  #gl = null;
  #program = null;
  #buffer = null;
  #attributes = {};
  #uniforms = {};
  #texture = null;
  #canvas = null;
  #context = null;
  #stale = true;
  #startProgress = 0;
  #neutralProgress = 0;
  #lastAction = "Ready";
  #captureLabel = "REC: CHECKING";
  #captureColor = "#72e2af";

  constructor() {
    this.#canvas = document.createElement("canvas");
    this.#canvas.width = CANVAS.width;
    this.#canvas.height = CANVAS.height;
    this.#context = this.#canvas.getContext("2d");
  }

  setStartProgress(progress) {
    const next = Math.max(0, Math.min(1, progress));
    if (Math.abs(next - this.#startProgress) >= 0.01) {
      this.#startProgress = next;
      this.#stale = true;
    }
  }

  setLastAction(action) {
    if (action !== this.#lastAction) {
      this.#lastAction = action;
      this.#stale = true;
    }
  }

  setCaptureStatus(status) {
    const phase = status.phase.toUpperCase();
    const detail = status.phase === "idle"
      ? (status.ready ? `READY | ${status.reason}` : status.blocked)
      : status.reason;
    const episode = status.episode == null ? "" : ` #${status.episode}`;
    const elapsed = status.phase === "recording" ? ` ${Math.floor(status.elapsed_s || 0)}s` : "";
    const label = `REC ${phase}${episode}${elapsed}: ${detail || ""}`;
    this.#captureColor = status.phase === "recording" ? "#ff8d81" : "#72e2af";
    if (label !== this.#captureLabel) {
      this.#captureLabel = label;
      this.#stale = true;
    }
  }

  setNeutralProgress(progress) {
    const next = Math.max(0, Math.min(1, progress));
    if (Math.abs(next - this.#neutralProgress) >= 0.01) {
      this.#neutralProgress = next;
      this.#stale = true;
    }
  }

  attach(gl) {
    this.#gl = gl;
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program));
    }
    this.#program = program;
    this.#attributes.corner = gl.getAttribLocation(program, "a_corner");
    for (const name of [
      "u_projection",
      "u_view",
      "u_half_extent",
      "u_center",
      "u_distance",
      "u_texture",
    ]) {
      this.#uniforms[name] = gl.getUniformLocation(program, name);
    }

    this.#buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
      gl.STATIC_DRAW,
    );

    this.#texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.#texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  }

  #button(x, y, width, height, color, label, fontSize = 34) {
    const context = this.#context;
    context.fillStyle = color;
    context.fillRect(x, y, width, height);
    context.fillStyle = "#ffffff";
    context.font = `bold ${fontSize}px sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(label, x + width / 2, y + height / 2);
  }

  #upload() {
    if (!this.#stale) {
      return;
    }
    const context = this.#context;
    const { width, height } = CANVAS;
    context.clearRect(0, 0, width, height);
    context.fillStyle = "rgba(5, 12, 18, 0.76)";
    context.fillRect(0, 0, width, height);

    this.#button(24, 20, 220, 88, "#b42318", "B  STOP");
    this.#button(264, 20, 320, 88, "#1769aa", "HOLD A  START", 31);

    context.fillStyle = "#263849";
    context.fillRect(264, 116, 320, 12);
    context.fillStyle = "#55d187";
    context.fillRect(264, 116, 320 * this.#startProgress, 12);

    context.fillStyle = "#173d37";
    context.fillRect(604, 20, 332, 88);
    context.fillStyle = "#ffffff";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.font = "bold 26px sans-serif";
    context.fillText("HOLD Y 2s", 770, 48);
    context.font = "21px sans-serif";
    context.fillText("NEUTRAL CALIBRATION", 770, 82);

    context.fillStyle = "#263849";
    context.fillRect(604, 116, 332, 12);
    context.fillStyle = "#38b99a";
    context.fillRect(604, 116, 332 * this.#neutralProgress, 12);

    context.textAlign = "center";
    context.fillStyle = "#d8e3ea";
    context.font = "bold 23px sans-serif";
    context.fillText(
      "TRIGGER: HOLD TO ALIGN  →  THEN CONTROL GRIPPER",
      width / 2,
      164,
    );

    context.fillStyle = "#ffffff";
    context.font = "bold 22px sans-serif";
    context.fillText(`LAST: ${this.#lastAction}`, width / 2, 210, width - 48);

    context.fillStyle = "#b8c7d1";
    context.font = "20px sans-serif";
    context.fillText(
      "X: RECORD / SUCCESS   |   B: STOP + FAIL   |   BREAKER: EMERGENCY",
      width / 2,
      250,
    );
    context.font = "18px sans-serif";
    context.fillStyle = this.#captureColor;
    context.fillText(
      this.#captureLabel,
      width / 2,
      281,
      width - 32,
    );

    const gl = this.#gl;
    gl.bindTexture(gl.TEXTURE_2D, this.#texture);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      this.#canvas,
    );
    this.#stale = false;
  }

  render(session, space, frame) {
    if (!this.#gl) {
      return;
    }
    const pose = frame.getViewerPose(space);
    if (!pose) {
      return;
    }
    const gl = this.#gl;
    const layer = session.renderState.baseLayer;
    gl.bindFramebuffer(gl.FRAMEBUFFER, layer.framebuffer);
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    this.#upload();

    const halfExtent = [
      PANEL.width / 2,
      ((PANEL.width / 2) * CANVAS.height) / CANVAS.width,
    ];
    gl.useProgram(this.#program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    gl.enableVertexAttribArray(this.#attributes.corner);
    gl.vertexAttribPointer(this.#attributes.corner, 2, gl.FLOAT, false, 0, 0);
    gl.uniform2f(this.#uniforms.u_half_extent, halfExtent[0], halfExtent[1]);
    gl.uniform2f(this.#uniforms.u_center, 0, PANEL.center);
    gl.uniform1f(this.#uniforms.u_distance, PANEL.distance);
    gl.uniform1i(this.#uniforms.u_texture, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.#texture);

    for (const view of pose.views) {
      const viewport = layer.getViewport(view);
      gl.viewport(viewport.x, viewport.y, viewport.width, viewport.height);
      gl.uniformMatrix4fv(
        this.#uniforms.u_projection,
        false,
        view.projectionMatrix,
      );
      gl.uniformMatrix4fv(
        this.#uniforms.u_view,
        false,
        view.transform.inverse.matrix,
      );
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    gl.disable(gl.BLEND);
  }
}

export function createSafetyControls() {
  return new SafetyControls();
}
