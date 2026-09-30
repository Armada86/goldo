import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCommand, parseControlCommand, etToUtc, formatTs, pnl } from "../src/index.js";

test("parseCommand: open commands, various phrasing", () => {
  assert.deepEqual(parseCommand("sell broker A"), { action: "open", tradeType: "Sell", brokerLetter: "A" });
  assert.deepEqual(parseCommand("Broker B buy"), { action: "open", tradeType: "Buy", brokerLetter: "B" });
  assert.deepEqual(parseCommand("BUY BROKER A"), { action: "open", tradeType: "Buy", brokerLetter: "A" });
  assert.deepEqual(parseCommand("please sell  broker  b now"), { action: "open", tradeType: "Sell", brokerLetter: "B" });
});

test("parseCommand: close commands", () => {
  assert.deepEqual(parseCommand("close broker A"), { action: "close", brokerLetter: "A" });
  assert.deepEqual(parseCommand("Close Broker B please"), { action: "close", brokerLetter: "B" });
});

test("parseCommand: rejects incomplete/unrelated text", () => {
  assert.equal(parseCommand("buy brokera"), null); // no space -- not a real command
  assert.equal(parseCommand("just buy something"), null);
  assert.equal(parseCommand("broker a"), null); // no direction, no close
  assert.equal(parseCommand("sell broker c"), null); // not a/b
  assert.equal(parseCommand("hello there"), null);
});

test("parseCommand: close takes priority over any direction word present", () => {
  // "close" plus a stray "buy" elsewhere in the sentence should still resolve to close, not open
  assert.deepEqual(parseCommand("close broker a, don't buy anything else"), { action: "close", brokerLetter: "A" });
});

test("pnl: Buy profits when price rises, loses when it falls", () => {
  assert.equal(pnl("Buy", 4300, 4310), 10);
  assert.equal(pnl("Buy", 4300, 4290), -10);
});

test("pnl: Sell profits when price falls, loses when it rises", () => {
  assert.equal(pnl("Sell", 4300, 4290), 10);
  assert.equal(pnl("Sell", 4300, 4310), -10);
});

test("formatTs: matches broker._format_ts()'s shape (YYYY-MM-DD HH:MM:SS TZ, America/New_York)", () => {
  const ts = formatTs(new Date("2026-09-25T14:06:57Z"));
  assert.match(ts, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (EDT|EST)$/);
});

const NOW = new Date("2026-09-30T15:00:00Z");

test("parseControlCommand: start/stop", () => {
  assert.deepEqual(parseControlCommand("stop trading"), { action: "stop" });
  assert.deepEqual(parseControlCommand("Start Trading"), { action: "start" });
  assert.equal(parseControlCommand("close broker a"), null);
});

test("parseControlCommand: the user's example (EDT, 7 till 10)", () => {
  const r = parseControlCommand("Thursday, 1st of October 2026. Stop trading from 7 till 10 o'clock.", NOW);
  assert.equal(r.action, "pause");
  assert.equal(r.start.toISOString(), "2026-10-01T11:00:00.000Z");
  assert.equal(r.end.toISOString(), "2026-10-01T14:00:00.000Z");
});

test("parseControlCommand: other date/time phrasings", () => {
  const a = parseControlCommand("stop trading October 2, 2026 from 9:30am to 10:45am", NOW);
  assert.equal(a.start.toISOString(), "2026-10-02T13:30:00.000Z");
  assert.equal(a.end.toISOString(), "2026-10-02T14:45:00.000Z");
  const b = parseControlCommand("stop trading 2026-10-02 2-4", NOW); // 2-4 => afternoon
  assert.equal(b.start.toISOString(), "2026-10-02T18:00:00.000Z");
  const c = parseControlCommand("stop trading on 5 Oct", NOW); // whole day, year defaults
  assert.equal(c.start.toISOString(), "2026-10-05T04:00:00.000Z");
  assert.equal(c.end.toISOString(), "2026-10-06T04:00:00.000Z");
});

test("parseControlCommand: winter time uses EST", () => {
  const r = parseControlCommand("stop trading 2026-12-01 from 7 to 10", NOW);
  assert.equal(r.start.toISOString(), "2026-12-01T12:00:00.000Z");
});

test("parseControlCommand: rejects bad input instead of guessing", () => {
  assert.equal(parseControlCommand("Friday, 1st of October 2026 stop trading from 7 till 10", NOW).action, "error"); // Oct 1 is Thursday
  assert.equal(parseControlCommand("stop trading from 10 till 7 on 2 Oct", NOW).action, "error");
  assert.equal(parseControlCommand("stop trading on friday", NOW).action, "error"); // unreadable date, not an immediate stop
  assert.equal(parseControlCommand("stop trading on 29 Sep 2026 from 7 to 10", NOW).action, "error"); // past
});

test("etToUtc: DST boundaries", () => {
  assert.equal(etToUtc(2026, 11, 1, 12, 0).toISOString(), "2026-11-01T17:00:00.000Z"); // after fall-back
  assert.equal(etToUtc(2026, 7, 1, 12, 0).toISOString(), "2026-07-01T16:00:00.000Z");
});
