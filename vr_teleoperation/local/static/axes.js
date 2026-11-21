// Copyright 2026
//
// Local WebXR visualization for the OpenArm Quest teleoperation workspace.

function pushBox(vertices, min, max, color) {
  const corners = [
    [min[0], min[1], min[2]],
    [max[0], min[1], min[2]],
    [max[0], max[1], min[2]],
    [min[0], max[1], min[2]],
    [min[0], min[1], max[2]],
    [max[0], min[1], max[2]],
    [max[0], max[1], max[2]],
    [min[0], max[1], max[2]],
  ];
  const faces = [
    [0, 1, 2, 0, 2, 3],
    [4, 6, 5, 4, 7, 6],
    [0, 4, 5, 0, 5, 1],
    [3, 2, 6, 3, 6, 7],
    [0, 3, 7, 0, 7, 4],
    [1, 5, 6, 1, 6, 2],
  ];
  for (const face of faces) {
    for (const index of face) {
      vertices.push(...corners[index], ...color);
    }
  }
}

const geometry = [];
const length = 0.16;
const halfWidth = 0.006;
pushBox(
  geometry,
  [0, -halfWidth, -halfWidth],
  [length, halfWidth, halfWidth],
  [1, 0.08, 0.08],
);
pushBox(
  geometry,
  [-halfWidth, 0, -halfWidth],
  [halfWidth, length, halfWidth],
  [0.08, 1, 0.08],
);
pushBox(
  geometry,
  [-halfWidth, -halfWidth, 0],
  [halfWidth, halfWidth, length],
  [0.08, 0.3, 1],
);
const VERTICES = new Float32Array(geometry);

const VERTEX_SHADER = `
  attribute vec3 a_position;
  attribute vec3 a_color;
  uniform mat4 u_projection;
  uniform mat4 u_view;
  uniform mat4 u_model;
  varying vec3 v_color;

  void main() {
    v_color = a_color;
    gl_Position = u_projection * u_view * u_model * vec4(a_position, 1.0);
  }
`;

const FRAGMENT_SHADER = `
  precision mediump float;
  varying vec3 v_color;

  void main() {
    gl_FragColor = vec4(v_color, 1.0);
  }
`;

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader));
  }
  return shader;
}

function program(gl) {
  const value = gl.createProgram();
  gl.attachShader(value, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
  gl.attachShader(value, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
  gl.linkProgram(value);
  if (!gl.getProgramParameter(value, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(value));
  }
  return value;
}

class AxesRenderer {
  #gl = null;
  #program = null;
  #buffer = null;
  #attributes = null;
  #uniforms = null;

  attach(gl) {
    this.#gl = gl;
    this.#program = program(gl);
    this.#buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    gl.bufferData(gl.ARRAY_BUFFER, VERTICES, gl.STATIC_DRAW);

    this.#attributes = {
      position: gl.getAttribLocation(this.#program, "a_position"),
      color: gl.getAttribLocation(this.#program, "a_color"),
    };
    this.#uniforms = {
      projection: gl.getUniformLocation(this.#program, "u_projection"),
      view: gl.getUniformLocation(this.#program, "u_view"),
      model: gl.getUniformLocation(this.#program, "u_model"),
    };
  }

  render(session, space, frame, clear) {
    if (!this.#gl) {
      return;
    }
    const viewerPose = frame.getViewerPose(space);
    if (!viewerPose) {
      return;
    }

    const transforms = [];
    for (const source of session.inputSources) {
      if (source.handedness === "none") {
        continue;
      }
      const pose = frame.getPose(source.targetRaySpace, space);
      if (pose) {
        transforms.push(pose.transform.matrix);
      }
    }

    const gl = this.#gl;
    const layer = session.renderState.baseLayer;
    gl.bindFramebuffer(gl.FRAMEBUFFER, layer.framebuffer);
    if (clear) {
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    }
    gl.disable(gl.DEPTH_TEST);
    gl.useProgram(this.#program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);

    const stride = 6 * Float32Array.BYTES_PER_ELEMENT;
    gl.enableVertexAttribArray(this.#attributes.position);
    gl.vertexAttribPointer(
      this.#attributes.position,
      3,
      gl.FLOAT,
      false,
      stride,
      0,
    );
    gl.enableVertexAttribArray(this.#attributes.color);
    gl.vertexAttribPointer(
      this.#attributes.color,
      3,
      gl.FLOAT,
      false,
      stride,
      3 * Float32Array.BYTES_PER_ELEMENT,
    );

    for (const view of viewerPose.views) {
      const viewport = layer.getViewport(view);
      gl.viewport(viewport.x, viewport.y, viewport.width, viewport.height);
      gl.uniformMatrix4fv(
        this.#uniforms.projection,
        false,
        view.projectionMatrix,
      );
      gl.uniformMatrix4fv(
        this.#uniforms.view,
        false,
        view.transform.inverse.matrix,
      );
      for (const transform of transforms) {
        gl.uniformMatrix4fv(this.#uniforms.model, false, transform);
        gl.drawArrays(gl.TRIANGLES, 0, VERTICES.length / 6);
      }
    }
  }
}

export function createAxesRenderer() {
  return new AxesRenderer();
}
