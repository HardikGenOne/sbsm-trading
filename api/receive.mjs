import { createHash, timingSafeEqual } from "node:crypto";
import { neon } from "@neondatabase/serverless";

const tradingViewIPs = new Set(["52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"]);

export function authenticate(request, expectedToken) {
  const supplied = new URL(request.url).searchParams.get("token") || "";
  const token = Buffer.from(supplied);
  const expected = Buffer.from(expectedToken || "");
  if (!expected.length || token.length !== expected.length || !timingSafeEqual(token, expected)) return 401;
  // Vercel overwrites this header at its public edge. No generic proxy chain.
  if (!tradingViewIPs.has(request.headers.get("x-vercel-forwarded-for"))) return 403;
  return 200;
}

export async function receive(request, sql, expectedToken) {
  if (request.method !== "POST") return Response.json({error: "POST required"}, {status: 405});
  const authentication = authenticate(request, expectedToken);
  if (authentication !== 200) return Response.json({error: "Unauthorized sender"}, {status: authentication});
  const reader = request.body?.getReader();
  if (!reader) return Response.json({error: "Missing body"}, {status: 422});
  const chunks = [];
  let length = 0;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > 16384) {
      await reader.cancel();
      return Response.json({error: "Body exceeds 16 KB"}, {status: 413});
    }
    chunks.push(Buffer.from(value));
  }
  let payload;
  try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { return Response.json({error: "Invalid JSON"}, {status: 422}); }
  if (!payload || Array.isArray(payload) || typeof payload !== "object")
    return Response.json({error: "JSON object required"}, {status: 422});
  const kind = new URL(request.url).searchParams.get("kind") === "test" ? "test" : "signal";
  if (kind === "test" && (Object.keys(payload).length !== 1 || typeof payload.test_id !== "string" || !/^[A-Za-z0-9_-]{8,48}$/.test(payload.test_id || "")))
    return Response.json({error: "Invalid test_id"}, {status: 422});
  if (kind === "signal" && (payload.strategy_id !== "SBSM" || typeof payload.event_id !== "string" || payload.event_id.length > 1200))
    return Response.json({error: "SBSM signal required"}, {status: 422});
  const body = JSON.stringify(payload);
  const key = createHash("sha256").update(kind + ":" + body).digest("hex");
  try {
    const rows = await sql`
      INSERT INTO webhook_receipts (receipt_key, received_ms, kind, payload, status, result)
      VALUES (${key}, floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint,
              ${kind}, ${body}::json, 'RECEIVED', '{}'::json)
      ON CONFLICT (receipt_key) DO UPDATE SET receipt_key = EXCLUDED.receipt_key
      RETURNING id, received_ms
    `;
    return Response.json({status: "RECEIVED", receipt_id: rows[0].id, received_ms: Number(rows[0].received_ms)}, {status: 202});
  } catch {
    // No acknowledgement until durable storage confirms the insert.
    return Response.json({error: "Durable receipt unavailable"}, {status: 503});
  }
}

export default {
  async fetch(request) {
    const databaseURL = process.env.SBSM_RECEIVER_DATABASE_URL;
    if (!databaseURL) return Response.json({error: "Receiver not configured"}, {status: 503});
    return receive(request, neon(databaseURL, {fetchOptions: {signal: AbortSignal.timeout(2000)}}), process.env.SBSM_RECEIVER_TOKEN);
  },
};
