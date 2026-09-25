import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCommand, formatTs, pnl } from "../src/index.js";

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
