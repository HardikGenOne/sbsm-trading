import {test} from "node:test";
import assert from "node:assert/strict";
import {receive} from "../../api/receive.mjs";

const token = "s".repeat(40);
function request(payload, ip="52.89.214.238", supplied=token) {
  return new Request("https://example.vercel.app/api/receive?kind=test&token="+supplied, {
    method:"POST", headers:{"x-vercel-forwarded-for":ip}, body:JSON.stringify(payload),
  });
}
test("requires secret and exact edge source before using storage", async () => {
  const never = () => assert.fail("Unexpected storage access");
  assert.equal((await receive(request({test_id:"test-0001"},"1.2.3.4"),never,token)).status,403);
  assert.equal((await receive(request({test_id:"test-0001"},"52.89.214.238","x"),never,token)).status,401);
  assert.equal((await receive(request({test_id:12345678}),never,token)).status,422);
});
test("acknowledges only after durable storage succeeds", async () => {
  const stored = await receive(request({test_id:"test-0001"}), async () => [{id:1,received_ms:1234}], token);
  assert.equal(stored.status,202);
  assert.equal((await stored.json()).receipt_id,1);
  const failed = await receive(request({test_id:"test-0001"}), async () => {throw new Error("private");}, token);
  assert.equal(failed.status,503);
  assert.equal((await failed.text()).includes("private"),false);
});
test("rejects malformed or oversized messages and ambiguous source headers", async () => {
  const never = () => assert.fail("Unexpected storage access");
  const mixed = request({test_id:"test-0001"},"52.89.214.238, 1.2.3.4");
  assert.equal((await receive(mixed,never,token)).status,403);
  assert.equal((await receive(request({test_id:"a".repeat(17000)}),never,token)).status,413);
  const malformed = new Request("https://example.vercel.app/api/receive?token="+token, {
    method:"POST",headers:{"x-vercel-forwarded-for":"52.89.214.238"},body:"{",
  });
  assert.equal((await receive(malformed,never,token)).status,422);
});
