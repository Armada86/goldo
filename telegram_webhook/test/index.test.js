import { test } from "node:test";
import assert from "node:assert/strict";
import { brokerStatus, STATUS_RE, parseBrokerScope, evaluatePause, nextWeekdayBoundary, isMarketClosedAt, inEntryWindow, tradingState, REARM_RE, RUN_TA_RE, SLA_RE, BRA_RE, parseStopLoss, parseTrail, parseCommand, parseControlCommand, etToUtc, formatTs, pnl } from "../src/index.js";

test("parseCommand: open commands, various phrasing", () => {
  assert.deepEqual(parseCommand("sell broker A"), { action: "open", tradeType: "Sell", brokerLetter: "A" });
  assert.deepEqual(parseCommand("Broker B buy"), { action: "open", tradeType: "Buy", brokerLetter: "B" });
  assert.deepEqual(parseCommand("BUY BROKER A"), { action: "open", tradeType: "Buy", brokerLetter: "A" });
  assert.deepEqual(parseCommand("please sell  broker  b now"), { action: "open", tradeType: "Sell", brokerLetter: "B" });
});

test("parseCommand: Broker F open/close", () => {
  assert.deepEqual(parseCommand("buy broker F"), { action: "open", tradeType: "Buy", brokerLetter: "F" });
  assert.deepEqual(parseCommand("Sell Broker f"), { action: "open", tradeType: "Sell", brokerLetter: "F" });
  assert.deepEqual(parseCommand("broker F buy"), { action: "open", tradeType: "Buy", brokerLetter: "F" });
  assert.deepEqual(parseCommand("close broker F"), { action: "close", brokerLetter: "F" });
  assert.equal(parseCommand("buy brokerf"), null);
  assert.equal(parseCommand("sell broker g"), null);
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
  assert.deepEqual(parseControlCommand("stop trading"), { action: "stop", brokers: [] });
  assert.deepEqual(parseControlCommand("Start Trading"), { action: "start", brokers: [] });
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

test("run TA: matches its phrasings only", () => {
  assert.ok(RUN_TA_RE.test("run TA"));
  assert.ok(RUN_TA_RE.test("Run technical analysis please"));
  assert.ok(!RUN_TA_RE.test("run tag"));
  assert.ok(!RUN_TA_RE.test("stop trading"));
});

test("parseControlCommand: '.' works as the minutes separator (10.30 am)", () => {
  const r = parseControlCommand("Stop trading thursday 1st of oct from 7am to 10.30 am", NOW);
  assert.equal(r.action, "pause");
  assert.equal(r.start.toISOString(), "2026-10-01T11:00:00.000Z");
  assert.equal(r.end.toISOString(), "2026-10-01T14:30:00.000Z");
  const s = parseControlCommand("stop trading 2 oct 2026 from 9.15 till 10.45", NOW);
  assert.equal(s.start.toISOString(), "2026-10-02T13:15:00.000Z");
  assert.equal(s.end.toISOString(), "2026-10-02T14:45:00.000Z");
});

test("rearm levels: matches its phrasings only", () => {
  assert.ok(REARM_RE.test("rearm levels"));
  assert.ok(REARM_RE.test("Re-arm all levels"));
  assert.ok(REARM_RE.test("REARM LEVEL"));
  assert.ok(!REARM_RE.test("rearm"));
  assert.ok(!REARM_RE.test("stop trading"));
});

test("make SL: sets the stop-loss from its phrasings", () => {
  assert.deepEqual(parseStopLoss("make SL 15"), { value: 15 });
  assert.deepEqual(parseStopLoss("Make SL 10"), { value: 10 });
  assert.deepEqual(parseStopLoss("set stop loss to 12.5"), { value: 12.5 });
  assert.deepEqual(parseStopLoss("make the stop-loss $20"), { value: 20 });
  assert.deepEqual(parseStopLoss("make SL 7,5"), { value: 7.5 });
});

test("make SL: rejects out-of-range values and ignores unrelated text", () => {
  assert.ok(parseStopLoss("make SL 0").error);
  assert.ok(parseStopLoss("make SL 500").error);
  assert.equal(parseStopLoss("stop trading"), null);
  assert.equal(parseStopLoss("SL 15"), null);
  assert.equal(parseStopLoss("make it rain"), null);
});

test("make trail: activation first, then distance", () => {
  assert.deepEqual(parseTrail("make trail 3 10"), { activation: 3, distance: 10 });
  assert.deepEqual(parseTrail("Make trail 3/10"), { activation: 3, distance: 10 });
  assert.deepEqual(parseTrail("make trail 3 / 10"), { activation: 3, distance: 10 });
  assert.deepEqual(parseTrail("make trail 3, 10"), { activation: 3, distance: 10 });
  assert.deepEqual(parseTrail("make trail 3 and 10"), { activation: 3, distance: 10 });
  assert.deepEqual(parseTrail("make trail $2.5 $7.5"), { activation: 2.5, distance: 7.5 });
  assert.deepEqual(parseTrail("set trailing stop to 7 7"), { activation: 7, distance: 7 });
  assert.ok(parseTrail("make trail 3 500").error);
});

test("make trail: distance, with activation defaulting to the same number", () => {
  assert.deepEqual(parseTrail("make trail 7"), { activation: 7, distance: 7 });
  assert.deepEqual(parseTrail("Set trailing stop to $5.5"), { activation: 5.5, distance: 5.5 });
  assert.deepEqual(parseTrail("change the trailing-stop 8"), { activation: 8, distance: 8 });
});

test("make trail: optional activation", () => {
  assert.deepEqual(parseTrail("make trail 6 activate 8"), { activation: 8, distance: 6 });
  assert.deepEqual(parseTrail("make trail 6 after $9"), { activation: 9, distance: 6 });
  assert.deepEqual(parseTrail("make trail 6, activation at 4"), { activation: 4, distance: 6 });
});

test("make trail: rejects out-of-range values, ignores unrelated and stop-loss text", () => {
  assert.ok(parseTrail("make trail 0").error);
  assert.ok(parseTrail("make trail 5 activate 500").error);
  assert.equal(parseTrail("make SL 15"), null);
  assert.equal(parseTrail("stop trading"), null);
  assert.equal(parseStopLoss("make trail 7"), null);
  assert.equal(parseTrail("make it rain"), null);
});

test("start SLA: matches its phrasings only", () => {
  assert.ok(SLA_RE.test("start SLA"));
  assert.ok(SLA_RE.test("Start stop loss analysis"));
  assert.ok(SLA_RE.test("run the stop-loss analysis"));
  assert.ok(!SLA_RE.test("SLA"));
  assert.ok(!SLA_RE.test("stop trading"));
  assert.ok(!SLA_RE.test("make SL 15"));
  assert.ok(!SLA_RE.test("start trading"));
});

test("start BRA: matches its phrasings only", () => {
  assert.ok(BRA_RE.test("start BRA"));
  assert.ok(BRA_RE.test("Start block rules analysis"));
  assert.ok(BRA_RE.test("run the block-rules analysis"));
  assert.ok(!BRA_RE.test("BRA"));
  assert.ok(!BRA_RE.test("start SLA"));
  assert.ok(!BRA_RE.test("stop trading"));
  assert.ok(!BRA_RE.test("start trading"));
});

test("parseBrokerScope: no name = all brokers ([]), a name = only that broker", () => {
  assert.deepEqual(parseBrokerScope("stop trading"), []);
  assert.deepEqual(parseBrokerScope("stop trading broker F"), ["F"]);
  assert.deepEqual(parseBrokerScope("Stop trading F"), ["F"]);
  assert.deepEqual(parseBrokerScope("start trading for broker b"), ["B"]);
  assert.deepEqual(parseBrokerScope("stop trading broker A and broker B"), ["A", "B"]);
  assert.deepEqual(parseBrokerScope("stop trading brokers a, f"), ["A", "F"]);
  assert.deepEqual(parseBrokerScope("stop trading broker B and F"), ["B", "F"]);
  assert.deepEqual(parseBrokerScope("stop trading on friday"), []); // "friday" is not a broker letter
});

test("parseControlCommand: per-broker stop/start/pause", () => {
  assert.deepEqual(parseControlCommand("stop trading broker F"), { action: "stop", brokers: ["F"] });
  assert.deepEqual(parseControlCommand("start trading B"), { action: "start", brokers: ["B"] });
  const r = parseControlCommand("Thursday, 1st of October 2026. Stop trading broker F from 7 till 10", NOW);
  assert.equal(r.action, "pause");
  assert.deepEqual(r.brokers, ["F"]);
  assert.equal(r.start.toISOString(), "2026-10-01T11:00:00.000Z");
  assert.deepEqual(parseControlCommand("Thursday, 1st of October 2026. Stop trading from 7 till 10", NOW).brokers, []);
  assert.equal(parseControlCommand("buy broker F"), null); // not a control command
});

test("STATUS_RE: broker status phrasings", () => {
  for (const ok of ["broker status", "Brokers status", "broker's status", "brokers' status", "status of brokers", "Status of the brokers"]) {
    assert.ok(STATUS_RE.test(ok), ok);
  }
  for (const no of ["status", "buy broker F", "stop trading", "broker F"]) assert.ok(!STATUS_RE.test(no), no);
});

const ms = (iso) => new Date(iso).getTime();

test("nextWeekdayBoundary: skips the weekend", () => {
  // Friday 2026-10-09 22:00 UTC (6pm EDT) -> next 7am ET weekday is Monday 12 Oct 11:00 UTC
  assert.equal(new Date(nextWeekdayBoundary(ms("2026-10-09T22:00:00Z"), 7)).toISOString(), "2026-10-12T11:00:00.000Z");
  // Monday 08:00 EDT -> next 5pm ET is the same day, 21:00 UTC
  assert.equal(new Date(nextWeekdayBoundary(ms("2026-10-12T12:00:00Z"), 17)).toISOString(), "2026-10-12T21:00:00.000Z");
});

test("evaluatePause: stop lasts to the next 7am, start overrides a pause that began before it", () => {
  const set = ms("2026-10-12T15:00:00Z"); // Mon 11am ET
  assert.ok(evaluatePause(ms("2026-10-12T16:00:00Z"), "stopped", set, []));
  assert.equal(evaluatePause(ms("2026-10-13T11:30:00Z"), "stopped", set, []), null); // Tue 7:30am ET: window reopened
  const pause = { start: ms("2026-10-12T14:00:00Z"), end: ms("2026-10-12T20:00:00Z") };
  assert.ok(evaluatePause(ms("2026-10-12T16:00:00Z"), null, null, [pause]));
  assert.equal(evaluatePause(ms("2026-10-12T16:00:00Z"), "started", set, [pause]), null); // started after the pause began
});

test("tradingState: market closed / outside hours / paused / trading", () => {
  assert.match(tradingState(ms("2026-10-11T20:00:00Z"), null, []), /market closed/); // Sunday 4pm ET
  assert.match(tradingState(ms("2026-10-12T05:00:00Z"), null, []), /outside trading hours/); // Mon 1am ET
  assert.equal(tradingState(ms("2026-10-12T15:00:00Z"), null, []), null); // Mon 11am ET
  assert.match(tradingState(ms("2026-10-12T15:00:00Z"), { mode: "stopped", setTs: ms("2026-10-12T14:00:00Z") }, []), /stopped/);
  assert.ok(isMarketClosedAt(ms("2026-10-09T21:30:00Z")) && !isMarketClosedAt(ms("2026-10-11T22:30:00Z")));
  assert.ok(inEntryWindow(ms("2026-10-12T11:00:00Z")) && !inEntryWindow(ms("2026-10-12T21:00:00Z")));
});

// A fake Neon `sql` tag: answers each query by a distinctive fragment of its text.
function fakeSql(answers) {
  return async (strings) => {
    const q = strings.join("?");
    for (const [needle, rows] of answers) if (q.includes(needle)) return rows;
    return [];
  };
}

test("brokerStatus: per-broker lines, open trade and watcher heartbeat", async () => {
  const realNow = Date.now;
  Date.now = () => ms("2026-10-12T15:00:00Z"); // Monday 11am ET: market open, inside trading hours
  try {
    const text = await brokerStatus(
      fakeSql([
        ["FROM trading_override", []],
        ["FROM broker_trading_override", [{ broker: "B", mode: "stopped", set_ts: "2026-10-12T14:00:00Z" }]],
        ["FROM trading_pauses", []],
        ["to_regclass('forex_b_trades')", [{ t: "forex_b_trades" }]],
        ["to_regclass('forex_b_heartbeat')", [{ t: "forex_b_heartbeat" }]],
        ["FROM trades WHERE", []],
        ["FROM broker_b_trades WHERE", []],
        ["FROM forex_b_trades WHERE", [{ trade_type: "Buy", entry_price: 4190.87, rule_name: "TA-Zone-buy", mode: "live" }]],
        ["FROM forex_b_heartbeat", [{ ts: "2026-10-12T14:59:40Z", mode: "live" }]],
      ])
    );
    assert.match(text, /Broker A: ✅ TRADING/);
    assert.match(text, /Broker B: ⛔ NOT TRADING — stopped by Telegram command/);
    assert.match(text, /Broker F \(live\): ✅ TRADING/);
    assert.match(text, /open: Buy 1 oz @ \$4190\.87 \(TA-Zone-buy\)/);
    assert.match(text, /watcher active 20s ago/);
  } finally {
    Date.now = realNow;
  }
});
