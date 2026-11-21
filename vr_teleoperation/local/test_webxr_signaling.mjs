// Exercise the real signaling function without WebRTC or robot connections.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";

const code = (await fs.readFile(new URL("./static/connection.js", import.meta.url), "utf8"))
  .replace(/^export /gm, "") + "\nglobalThis.signalOffer = postOffer;";

for (const page of [
  "http://localhost:8080/",
  "http://localhost:8080/?v=1015",
  "http://localhost:8080/ui/4319d6b571ab/index.html",
]) {
  const requests = [];
  let status = 200;
  const context = vm.createContext({
    fetch: async (path, options) => {
      requests.push({ url: new URL(path, page), options });
      return {
        ok: status === 200, status,
        json: async () => ({ sdp: "test-answer" }),
      };
    },
  });
  vm.runInContext(code, context);
  assert.equal(await context.signalOffer("test-offer"), "test-answer");
  const request = requests.at(-1);
  assert.equal(request.url.href, "http://localhost:8080/offer");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(request.options.body), { sdp: "test-offer" });

  status = 405;
  await assert.rejects(context.signalOffer("test-offer"), /signaling failed: 405/);
}

console.log("PASS: root, legacy and versioned pages signal POST /offer; HTTP errors propagate");
