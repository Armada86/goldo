/**
 * Telegram webhook handler for manually opening/closing Broker A/B positions -- the real-time
 * replacement for a polling job, on the user's explicit request ("the moment I push send", "do
 * not program anything as a poll"). This is a Cloudflare Worker, not Python, because nothing else
 * in this project can receive an inbound webhook at all: GitHub Actions (everything else here) can
 * only be *triggered by* an API call this project makes, never the reverse, so there is no way to
 * point Telegram's webhook at a GitHub Actions workflow. A Worker is the smallest "no server to
 * manage" way to get a fixed, always-reachable public HTTPS URL that Telegram can POST to the
 * instant a message arrives -- it sits dormant between requests and wakes in milliseconds, so it's
 * still no persistent process to babysit, just a different kind of infrastructure than the rest of
 * this repo.
 *
 * Talks to the SAME Neon Postgres database as the Python side, via Neon's own serverless driver
 * (@neondatabase/serverless -- HTTP-based single queries, built specifically for edge runtimes
 * like Workers that can't hold a normal TCP connection pool open). Writes go straight into the
 * same `trades`/`broker_b_trades` tables broker.py/broker_b.py already use, with the exact same
 * columns, so a Telegram-opened trade is indistinguishable in shape from an algorithmic one --
 * only `rule_name` ("Telegram-buy"/"Telegram-sell") and `triggering_alerts` ("Manual (Telegram
 * command)") mark it as manual. The existing Python poll (check_broker_trades()/
 * check_broker_b_trades()) will happily pick up and auto-close a Telegram-opened trade at its
 * normal $10 target if a manual close command never arrives first -- the two paths don't conflict,
 * they just both watch the same `status = 'Open'` row.
 *
 * Broker F (forex_watcher.py -- Broker B's rules on the FOREX.com DEMO account) was added to the commands on the user's
 * explicit request (10 Oct 2026). The Worker does NOT talk to forex.com: the order code, its XAU/USD-only locks and the stop
 * handling live in Python, so "buy/sell/close broker F" only queues a row in forex_b_commands and dispatches forex_watch.yml;
 * the watcher loop (already ticking every 10 s) executes it and sends the fill/close message itself. The old forex_broker.py
 * is still not reachable from here.
 *
 * Commands recognized in an incoming Telegram message's text (case-insensitive, forgiving about
 * word order/spacing):
 *   "buy broker a" / "sell broker B" / "Broker F buy"  -> open a Buy/Sell position on that broker (F: a real 1 oz
 *                                                          market order on the demo account, queued for the watcher)
 *   "close broker a" / "close broker B"                -> close that broker's open position NOW,
 *                                                          at the current spot price, regardless
 *                                                          of unrealized P/L (unlike the automatic
 *                                                          trailing stop, a manual
 *                                                          close is an unconditional override)
 *   "stop trading"                                     -> close BOTH brokers' open positions at spot and
 *                                                          pause all trading until the program's own
 *                                                          trading window next opens (7am ET weekdays)
 *   "start trading"                                    -> resume trading for both brokers until the
 *                                                          program's own window next closes (5pm ET)
 *   "<date>. Stop trading from 7 till 10"              -> schedule a pause (times are America/New_York,
 *                                                          e.g. "Thursday, 1st of October 2026. Stop
 *                                                          trading from 7 till 10 o'clock"); no date =
 *                                                          immediate stop, date without times = all day
 *   (the Python poll enforces all three -- see trading_control.py -- this Worker only records them)
 *   "run TA"                                           -> dispatch the ta_forecast.yml GitHub workflow (the same
 *                                                          job cron-job.org runs at 7am/12pm). It writes a
 *                                                          new ta_forecasts row, which is by definition the
 *                                                          latest one -- what Broker B trades and the
 *                                                          dashboard shows -- and sends it to Telegram.
 *                                                          Needs the GITHUB_DISPATCH_TOKEN secret.
 *   "rearm levels"                                     -> Broker B: forget every earlier trade/stop-out on the
 *                                                          TA levels, so each level can trade again (up to
 *                                                          its normal per-level limit); the Python poll reads
 *                                                          the rearm time (broker_b_rearm table)
 *   "make SL 15" / "set stop loss 12.5"                -> set the stop-loss ($ per oz, 1-100) for BOTH brokers; it
 *                                                          stays at that value until changed again and also
 *                                                          applies to a trade that is already open
 *                                                          (stop_loss_setting table, read by broker.py)
 *   "make trail 3 10" / "make trail 7"                 -> set the trailing stop for BOTH brokers: the FIRST number is how much
 *                                                          profit switches it on (activation), the SECOND how far behind
 *                                                          the best price it then follows (distance); one number sets both.
 *                                                          "make trail 10 activate 3" also works (the keyword names the role).
 *                                                          Applies to open trades too (trailing_stop_setting table, read by broker.py)
 *   "start stop loss analysis" / "start SLA"           -> dispatch the stop_loss_analysis.yml GitHub workflow: it replays every
 *                                                          closed trade against a grid of stop-loss / trailing-stop
 *                                                          settings and sends the result here (Needs GITHUB_DISPATCH_TOKEN,
 *                                                          like "run TA"). When it advises a change it APPLIES it automatically
 *                                                          (same rows as make SL / make trail). Also runs by itself at 6:15 AM ET.
 *   "start block rules analysis" / "start BRA"          -> dispatch the block_rules_analysis.yml GitHub workflow: it replays every
 *                                                          Broker B touch against DXY / ADX / RSI / ATR values, writes the
 *                                                          resulting rules to the block_rules table (read by broker_b.py) and
 *                                                          sends its report here (Needs GITHUB_DISPATCH_TOKEN, like "start SLA").
 * Anything else is silently ignored -- no reply -- so the chat doesn't become a bot that talks
 * back to every unrelated message.
 *
 * Security: verifies the `X-Telegram-Bot-Api-Secret-Token` header Telegram sends on every webhook
 * request (set via setWebhook's own `secret_token` param -- see the deployment instructions in
 * CLAUDE.md) so a request can't be forged by anyone who merely guesses this Worker's URL. Also
 * checks the message's own chat.id against TELEGRAM_CHAT_ID, same authorization check the earlier
 * polling design used, so only the authorized chat can issue commands even if the secret token
 * were somehow also known.
 */

import { neon } from "@neondatabase/serverless";

const TRAILING_STOP_ACTIVATION = 7.0; // must match broker.TRAILING_STOP_ACTIVATION (no fixed take-profit any more)
const TRAILING_STOP_DISTANCE = 7.0; // must match broker.TRAILING_STOP_DISTANCE

const TRADE_ALERT_PREFIX_A = "\u{1F535} "; // blue circle -- broker.TRADE_ALERT_PREFIX
const PROFIT_MARKER_A = "\u{1F7E2} "; // green circle -- broker.PROFIT_MARKER
const LOSS_MARKER_A = "\u{1F534} "; // red circle -- broker.LOSS_MARKER

const TRADE_ALERT_PREFIX_B = "\u{1F7E6} "; // blue square -- broker_b.TRADE_ALERT_PREFIX
const PROFIT_MARKER_B = "\u{1F7E9} "; // green square -- broker_b.PROFIT_MARKER
const LOSS_MARKER_B = "\u{1F7E5} "; // red square -- broker_b.LOSS_MARKER

const TRADE_ALERT_PREFIX_F = "\u{1F7EA} "; // purple square -- forex_watcher.TRADE_PREFIX (Broker F, the FOREX.com demo account)

const REJECT_MARKER = "⛔ "; // no-entry sign -- same as broker.BLOCKED_MARKER/broker_b.BLOCKED_MARKER

const DIRECTION_RE = /\b(buy|sell)\b/i;
const BROKER_RE = /\bbroker\s+([abf])\b/i;
const CLOSE_RE = /\bclose\b/i;

/** Mirrors broker._format_ts(): America/New_York, "YYYY-MM-DD HH:MM:SS TZ". */
function formatTs(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")} ${get("timeZoneName")}`;
}

function pnl(tradeType, entryPrice, currentPrice) {
  return tradeType === "Buy" ? currentPrice - entryPrice : entryPrice - currentPrice;
}

/**
 * Returns {action: "open", tradeType, brokerLetter} | {action: "close", brokerLetter} | null.
 * A "close" command only needs the broker letter; an "open" command needs both a buy/sell word
 * and the broker letter, or it's not recognized (matches parse_command()'s original design from
 * the polling version -- ambiguous input is ignored, not guessed at).
 */
function parseCommand(text) {
  const brokerMatch = BROKER_RE.exec(text);
  if (!brokerMatch) return null;
  const brokerLetter = brokerMatch[1].toUpperCase();

  if (CLOSE_RE.test(text)) {
    return { action: "close", brokerLetter };
  }
  const directionMatch = DIRECTION_RE.exec(text);
  if (!directionMatch) return null;
  const tradeType = directionMatch[1][0].toUpperCase() + directionMatch[1].slice(1).toLowerCase();
  return { action: "open", tradeType, brokerLetter };
}

const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
const WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const MONTH_PAT = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)";
const DAY_FIRST_RE = new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(?:of\\s+)?${MONTH_PAT}\\b(?:,?\\s+(\\d{4}))?`, "i");
const MONTH_FIRST_RE = new RegExp(`\\b${MONTH_PAT}\\s+(\\d{1,2})(?:st|nd|rd|th)?\\b(?:,?\\s+(\\d{4}))?`, "i");
const ISO_DATE_RE = /\b(\d{4})-(\d{1,2})-(\d{1,2})\b/;
const WEEKDAY_RE = new RegExp(`\\b(${WEEKDAYS.join("|")})\\b`, "i");
const TIME_RANGE_RE = /(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*(?:o'?clock\s*)?(?:-|\u2013|to|till|until|til)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?/i;

function etParts(ms) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric", hourCycle: "h23",
  }).formatToParts(new Date(ms));
  const get = (t) => Number(parts.find((p) => p.type === t).value);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute"), second: get("second") };
}

/** America/New_York wall-clock time -> the real instant (handles EST/EDT; day may overflow, e.g. day+1). */
function etToUtc(year, month, day, hour, minute) {
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  let instant = wall;
  for (let i = 0; i < 2; i++) {
    const p = etParts(instant);
    const shown = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
    instant += wall - shown; // nudge by however far the ET reading is from what we wanted
  }
  return new Date(instant);
}

function to24h(hour, meridiem) {
  if (meridiem === "am") return hour === 12 ? 0 : hour;
  if (meridiem === "pm") return hour === 12 ? 12 : hour + 12;
  return hour;
}

/**
 * Trading-control commands. Returns null (not a control command), or one of
 * {action: "stop"} | {action: "start"} | {action: "pause", start: Date, end: Date} | {action: "error", message}.
 * All clock times are America/New_York. Times without am/pm: 1-6 are read as pm (trading only runs
 * 7am-5pm ET, so "from 2 till 5" can only mean the afternoon); 7-12 and up as written.
 */
function parseControlCommand(text, now = new Date()) {
  if (/\bstart\s+trading\b/i.test(text)) return { action: "start" };
  if (!/\bstop\s+trading\b/i.test(text)) return null;

  let year, month, day, matched;
  let m;
  if ((m = ISO_DATE_RE.exec(text))) {
    [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
    matched = m[0];
  } else if ((m = DAY_FIRST_RE.exec(text))) {
    day = Number(m[1]);
    month = MONTHS.findIndex((n) => n.startsWith(m[2].toLowerCase().slice(0, 3))) + 1;
    year = m[3] ? Number(m[3]) : undefined;
    matched = m[0];
  } else if ((m = MONTH_FIRST_RE.exec(text))) {
    month = MONTHS.findIndex((n) => n.startsWith(m[1].toLowerCase().slice(0, 3))) + 1;
    day = Number(m[2]);
    year = m[3] ? Number(m[3]) : undefined;
    matched = m[0];
  }

  const hasDateWord = WEEKDAY_RE.test(text) || new RegExp(`\\b${MONTH_PAT}\\b`, "i").test(text);
  if (!matched) {
    if (hasDateWord || /\d/.test(text)) {
      return { action: "error", message: 'Could not read the date. Try e.g. "Thursday, 1st of October 2026. Stop trading from 7 till 10".' };
    }
    return { action: "stop" };
  }
  if (year === undefined) year = etParts(now.getTime()).year;
  if (month < 1 || day < 1 || day > 31 || new Date(Date.UTC(year, month - 1, day)).getUTCDate() !== day) {
    return { action: "error", message: `"${matched}" is not a valid date.` };
  }
  const wd = WEEKDAY_RE.exec(text);
  if (wd && WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()] !== wd[1].toLowerCase()) {
    const real = WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
    return { action: "error", message: `${month}/${day}/${year} is a ${real}, not ${wd[1]}. Nothing scheduled.` };
  }

  const rest = text.replace(matched, " ");
  const t = TIME_RANGE_RE.exec(rest);
  let start, end;
  if (!t) {
    start = etToUtc(year, month, day, 0, 0);
    end = etToUtc(year, month, day + 1, 0, 0);
  } else {
    let [h1, m1, ap1, h2, m2, ap2] = [Number(t[1]), Number(t[2] ?? 0), t[3]?.toLowerCase(), Number(t[4]), Number(t[5] ?? 0), t[6]?.toLowerCase()];
    if (h1 > 24 || h2 > 24 || m1 > 59 || m2 > 59) {
      return { action: "error", message: "Could not read the hours." };
    }
    if (!ap1 && !ap2) {
      if (h1 >= 1 && h1 <= 6) h1 += 12;
      if (h2 >= 1 && h2 <= 6) h2 += 12;
    } else {
      ap1 = ap1 ?? ap2;
      ap2 = ap2 ?? ap1;
      h1 = to24h(h1, ap1);
      h2 = to24h(h2, ap2);
    }
    if (h2 * 60 + m2 <= h1 * 60 + m1) {
      return { action: "error", message: "The end time must be after the start time." };
    }
    start = etToUtc(year, month, day, h1, m1);
    end = etToUtc(year, month, day, h2, m2);
  }
  if (end <= now) return { action: "error", message: "That window is already in the past. Nothing scheduled." };
  return { action: "pause", start, end };
}

async function ensureControlTables(sql) {
  await sql`CREATE TABLE IF NOT EXISTS trading_override (id INTEGER PRIMARY KEY CHECK (id = 1), mode TEXT NOT NULL, set_ts TIMESTAMPTZ NOT NULL)`;
  await sql`CREATE TABLE IF NOT EXISTS trading_pauses (id SERIAL PRIMARY KEY, start_ts TIMESTAMPTZ NOT NULL, end_ts TIMESTAMPTZ NOT NULL, created_ts TIMESTAMPTZ NOT NULL DEFAULT NOW())`;
}

async function setOverride(sql, mode) {
  await sql`
    INSERT INTO trading_override (id, mode, set_ts) VALUES (1, ${mode}, ${new Date().toISOString()})
    ON CONFLICT (id) DO UPDATE SET mode = EXCLUDED.mode, set_ts = EXCLUDED.set_ts
  `;
}

async function stopTrading(sql, apiKey, env) {
  await ensureControlTables(sql);
  await setOverride(sql, "stopped"); // first, so the poll stops opening things even if a close below fails
  const lines = ["\u{1F6D1} Trading STOPPED for Broker A, Broker B and Broker F."];
  const openA = await sql`SELECT id FROM trades WHERE status = 'Open' LIMIT 1`;
  const openB = await sql`SELECT id FROM broker_b_trades WHERE status = 'Open' LIMIT 1`;
  if (openA.length > 0) lines.push(await closeBrokerA(sql, apiKey));
  if (openB.length > 0) lines.push(await closeBrokerB(sql, apiKey));
  if (await brokerFHasOpenTrade(sql)) {
    // The watcher closes it itself within one tick (trading_pause_reason); make sure a watcher loop is running to do so.
    const dispatchError = await dispatchWorkflow(env, "forex_watch.yml", "the Broker F watcher");
    lines.push(
      `${TRADE_ALERT_PREFIX_F.trimEnd()} BROKER F: its open position is closed on forex.com within seconds by the watcher.` +
        (dispatchError ? ` (${dispatchError})` : "")
    );
  }
  lines.push('No new trades until the next automatic trading window opens (7am ET, weekdays) or you send "start trading".');
  return lines.join("\n");
}

async function startTrading(sql) {
  await ensureControlTables(sql);
  await setOverride(sql, "started");
  return '\u25B6\uFE0F Trading STARTED for Broker A, Broker B and Broker F. It runs until the program\'s own trading window closes (5pm ET), or until you send "stop trading".';
}

async function schedulePause(sql, start, end) {
  await ensureControlTables(sql);
  await sql`INSERT INTO trading_pauses (start_ts, end_ts) VALUES (${start.toISOString()}, ${end.toISOString()})`;
  return (
    `\u23F8 Pause scheduled for Broker A, Broker B and Broker F:\n${formatTs(start)} -> ${formatTs(end)}\n` +
    "No new trades in that window; any open position is closed at the first poll (every 5 min) inside it (Broker F: within seconds)."
  );
}

const REARM_RE = /\bre-?arm\s+(?:all\s+)?levels?\b/i;

async function rearmLevels(sql) {
  await sql`CREATE TABLE IF NOT EXISTS broker_b_rearm (id INTEGER PRIMARY KEY CHECK (id = 1), rearm_ts TIMESTAMPTZ NOT NULL)`;
  await sql`
    INSERT INTO broker_b_rearm (id, rearm_ts) VALUES (1, ${new Date().toISOString()})
    ON CONFLICT (id) DO UPDATE SET rearm_ts = EXCLUDED.rearm_ts
  `;
  return (
    `${TRADE_ALERT_PREFIX_B.trimEnd()}\u{1F501} BROKER B and BROKER F: all levels re-armed. Earlier trades and stop-outs no longer count, ` +
    "so each level can trade again. A level still needs a fresh approach and touch before it fires."
  );
}

const STOP_LOSS_RE = /\b(?:make|set|change)\s+(?:the\s+)?(?:sl|stop[\s-]?loss)\s*(?:to|at|=|:)?\s*\$?(\d+(?:[.,]\d+)?)/i;
const TRAIL_RE = /\b(?:make|set|change)\s+(?:the\s+)?trail(?:ing)?(?:[\s-]*stop)?\s*(?:to|at|=|:)?\s*\$?(\d+(?:[.,]\d+)?)([\s\S]*)$/i;
// After the first number: an explicit "activate 8" (then the FIRST number is the distance), or a second number (then the
// first is the activation and the second the distance: "make trail 3 10", "3/10", "3, 10", "3 and 10").
const TRAIL_KEYWORD_RE = /^\s*(?:,|and|with)?\s*(?:activat\w*|after|from|starting)\s*(?:at|when|to)?\s*\$?(\d+(?:[.,]\d+)?)/i;
const TRAIL_PAIR_RE = /^(?:\s*\/\s*|\s*,\s+|\s+and\s+|\s+)\$?(\d+(?:[.,]\d+)?)/i;
const STOP_LOSS_MIN = 1;
const STOP_LOSS_MAX = 100;

/** Returns null (not a stop-loss command), {error}, or {value} (dollars per oz). */
function parseStopLoss(text) {
  const m = STOP_LOSS_RE.exec(text);
  if (!m) return null;
  const value = Number(m[1].replace(",", "."));
  if (!(value >= STOP_LOSS_MIN && value <= STOP_LOSS_MAX)) {
    return { error: `Stop-loss must be between $${STOP_LOSS_MIN} and $${STOP_LOSS_MAX}. Nothing was changed.` };
  }
  return { value };
}

/**
 * Returns null (not a trail command), {error}, or {activation, distance} (dollars per oz).
 *   "make trail 3 10"            -> activation 3, distance 10 (activation first, then distance)
 *   "make trail 7"               -> both 7
 *   "make trail 10 activate 3"   -> distance 10, activation 3 (the keyword names the role)
 */
function parseTrail(text) {
  const m = TRAIL_RE.exec(text);
  if (!m) return null;
  const first = Number(m[1].replace(",", "."));
  const rest = m[2] || "";
  let activation = first;
  let distance = first;
  const keyword = TRAIL_KEYWORD_RE.exec(rest);
  const pair = keyword ? null : TRAIL_PAIR_RE.exec(rest);
  if (keyword) {
    activation = Number(keyword[1].replace(",", "."));
  } else if (pair) {
    distance = Number(pair[1].replace(",", "."));
  }
  for (const v of [distance, activation]) {
    if (!(v >= STOP_LOSS_MIN && v <= STOP_LOSS_MAX)) {
      return { error: `Trailing-stop values must be between $${STOP_LOSS_MIN} and $${STOP_LOSS_MAX}. Nothing was changed.` };
    }
  }
  return { activation, distance };
}

/** Display-only log of stop-setting changes (stop_settings_history, read by the dashboard); a NULL column = not changed. Never throws. */
async function logStopSettings(sql, stopLoss, activation, distance) {
  try {
    await sql`CREATE TABLE IF NOT EXISTS stop_settings_history (id SERIAL PRIMARY KEY, set_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(), source TEXT NOT NULL, stop_loss DOUBLE PRECISION, activation DOUBLE PRECISION, distance DOUBLE PRECISION)`;
    await sql`INSERT INTO stop_settings_history (source, stop_loss, activation, distance) VALUES ('telegram', ${stopLoss}, ${activation}, ${distance})`;
  } catch (e) {
    console.error("stop_settings_history log failed", e);
  }
}

async function setTrail(sql, activation, distance) {
  await sql`CREATE TABLE IF NOT EXISTS trailing_stop_setting (id INTEGER PRIMARY KEY CHECK (id = 1), activation DOUBLE PRECISION NOT NULL, distance DOUBLE PRECISION NOT NULL)`;
  await sql`
    INSERT INTO trailing_stop_setting (id, activation, distance) VALUES (1, ${activation}, ${distance})
    ON CONFLICT (id) DO UPDATE SET activation = EXCLUDED.activation, distance = EXCLUDED.distance
  `;
  await logStopSettings(sql, null, activation, distance);
  return (
    `\u{1F4C8} Trailing stop set for Broker A, Broker B and Broker F: it starts trailing once a trade is $${activation.toFixed(2)} in profit ` +
    `and then follows $${distance.toFixed(2)} behind the best price. It applies to open trades too, and it stays until you change it.`
  );
}

async function setStopLoss(sql, value) {
  await sql`CREATE TABLE IF NOT EXISTS stop_loss_setting (id INTEGER PRIMARY KEY CHECK (id = 1), stop_loss DOUBLE PRECISION NOT NULL)`;
  await sql`
    INSERT INTO stop_loss_setting (id, stop_loss) VALUES (1, ${value})
    ON CONFLICT (id) DO UPDATE SET stop_loss = EXCLUDED.stop_loss
  `;
  await logStopSettings(sql, value, null, null);
  return (
    `\u{1F6D1} Stop-loss set to $${value.toFixed(2)} for Broker A, Broker B and Broker F. ` +
    `It stays at $${value.toFixed(2)} until you change it, and applies to open trades too (no fixed take-profit: once a trade is $${TRAILING_STOP_ACTIVATION.toFixed(0)} up, the stop trails $${TRAILING_STOP_DISTANCE.toFixed(0)} behind its best price).`
  );
}

const RUN_TA_RE = /\brun\s+(?:ta|technical\s+analysis)\b/i;

/** Dispatches a GitHub Actions workflow via workflow_dispatch. Returns null on success, else the error reply text. */
async function dispatchWorkflow(env, workflowFile, what) {
  if (!env.GITHUB_DISPATCH_TOKEN) {
    return `${REJECT_MARKER}${what} is not set up: the GITHUB_DISPATCH_TOKEN secret is missing on the Worker.`;
  }
  const repo = env.GITHUB_REPO || "Armada86/goldo";
  const res = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${workflowFile}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "goldo-telegram-webhook",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (res.status !== 204) {
    return `${REJECT_MARKER}Could not start ${what} (GitHub replied ${res.status}). Check the GITHUB_DISPATCH_TOKEN permissions.`;
  }
  return null;
}

/** Triggers ta_forecast.yml via GitHub's workflow_dispatch API; returns the Telegram reply text. */
async function runTa(env) {
  const error = await dispatchWorkflow(env, "ta_forecast.yml", "the TA run");
  if (error) return error;
  return "\u{1F4C8} Running a new technical analysis. It takes a minute or two; when it arrives here it is the active forecast.";
}

const SLA_RE = /\b(?:start|run)\s+(?:the\s+)?(?:sla|stop[\s-]?loss\s+analysis)\b/i;

/** Triggers stop_loss_analysis.yml; the job sends its advice to this chat when it finishes. */
async function runSla(env) {
  const error = await dispatchWorkflow(env, "stop_loss_analysis.yml", "the stop loss analysis");
  if (error) return error;
  return (
    "\u{1F4CA} Stop loss analysis started. It replays every closed trade against different stop-loss and trailing-stop settings. " +
    "The result arrives here in a few minutes (it paces its price requests). If it advises a change, the new stop loss and trailing stop are applied automatically."
  );
}

const BRA_RE = /\b(?:start|run)\s+(?:the\s+)?(?:bra|block[\s-]?rules?\s+analysis)\b/i;

/** Triggers block_rules_analysis.yml; the job writes the new rules to the block_rules table and sends its report to this chat. */
async function runBra(env) {
  const error = await dispatchWorkflow(env, "block_rules_analysis.yml", "the block rules analysis");
  if (error) return error;
  return (
    "\u{1F6E1}\uFE0F Block rules analysis started. It replays every Broker B touch (trades and blocked touches) against different " +
    "DXY / ADX / RSI / ATR values. The report arrives here in a few minutes, and the resulting rules are written to the block_rules table, " +
    "which Broker B reads on its next poll."
  );
}

async function fetchGoldPrice(apiKey) {
  const res = await fetch(`https://api.twelvedata.com/price?symbol=XAU%2FUSD&apikey=${apiKey}`);
  const data = await res.json();
  const price = parseFloat(data.price);
  return Number.isFinite(price) ? price : null;
}

async function sendTelegram(botToken, chatId, text) {
  await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}


async function ensureForexCommandsTable(sql) {
  await sql`CREATE TABLE IF NOT EXISTS forex_b_commands (id SERIAL PRIMARY KEY, command TEXT NOT NULL, created_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(), status TEXT NOT NULL DEFAULT 'pending', handled_ts TIMESTAMPTZ, result TEXT)`;
}

/** True if Broker F (forex_b_trades) has an open trade; false if the table does not exist yet. */
async function brokerFHasOpenTrade(sql) {
  const exists = await sql`SELECT to_regclass('forex_b_trades') AS t`;
  if (!exists[0].t) return false;
  const open = await sql`SELECT id FROM forex_b_trades WHERE status = 'Open' LIMIT 1`;
  return open.length > 0;
}

/**
 * Broker F commands are queued for the Python watcher (forex_watcher.py), which places/closes the order on the forex.com demo
 * account and sends the fill message. The reply here only confirms the command was queued (or that nothing could be queued).
 */
async function queueBrokerF(sql, env, command) {
  await ensureForexCommandsTable(sql);
  await sql`INSERT INTO forex_b_commands (command) VALUES (${command})`;
  const dispatchError = await dispatchWorkflow(env, "forex_watch.yml", "the Broker F watcher");
  const what = command === "close" ? "close" : `${command === "buy" ? "Buy" : "Sell"} 1 oz XAU/USD`;
  return (
    `${TRADE_ALERT_PREFIX_F.trimEnd()} BROKER F: ${what} queued for the forex.com demo account. The watcher executes it within seconds ` +
    "and confirms here; if it cannot, it says why." +
    (dispatchError ? `\n(${dispatchError} If no watcher is running, the order waits up to 5 minutes, then expires.)` : "")
  );
}

async function openBrokerA(sql, tradeType, apiKey) {
  const open = await sql`SELECT id FROM trades WHERE status = 'Open' ORDER BY open_ts DESC LIMIT 1`;
  if (open.length > 0) {
    return `${TRADE_ALERT_PREFIX_A.trimEnd()}${REJECT_MARKER}BROKER A: cannot open ${tradeType} -- a trade is already open. Close it first.`;
  }
  const price = await fetchGoldPrice(apiKey);
  if (price === null) {
    return `${TRADE_ALERT_PREFIX_A.trimEnd()}${REJECT_MARKER}BROKER A: cannot open ${tradeType} -- gold spot price unavailable right now.`;
  }
  const now = new Date();
  const ruleName = `Telegram-${tradeType.toLowerCase()}`;
  const inserted = await sql`
    INSERT INTO trades (rule_name, trade_type, entry_price, open_ts, triggering_alerts, status)
    VALUES (${ruleName}, ${tradeType}, ${price}, ${now.toISOString()}, 'Manual (Telegram command)', 'Open')
    RETURNING id
  `;
  return (
    `${TRADE_ALERT_PREFIX_A}BROKER A #${inserted[0].id}: opened ${tradeType} 1 oz XAU/USD @ $${price.toFixed(2)} (rule ${ruleName}).\n` +
    `Trigger: Manual (Telegram command)\n` +
    `Filled: ${formatTs(now)}`
  );
}

async function closeBrokerA(sql, apiKey) {
  const rows = await sql`
    SELECT id, rule_name, trade_type, entry_price FROM trades
    WHERE status = 'Open' ORDER BY open_ts DESC LIMIT 1
  `;
  if (rows.length === 0) {
    return `${TRADE_ALERT_PREFIX_A.trimEnd()}${REJECT_MARKER}BROKER A: no open trade to close.`;
  }
  const trade = rows[0];
  const price = await fetchGoldPrice(apiKey);
  if (price === null) {
    return `${TRADE_ALERT_PREFIX_A.trimEnd()}${REJECT_MARKER}BROKER A: cannot close -- gold spot price unavailable right now.`;
  }
  const now = new Date();
  const p = pnl(trade.trade_type, trade.entry_price, price);
  await sql`
    UPDATE trades SET exit_price = ${price}, close_ts = ${now.toISOString()}, pnl = ${p}, status = 'Closed'
    WHERE id = ${trade.id}
  `;
  const marker = p >= 0 ? PROFIT_MARKER_A : LOSS_MARKER_A;
  const result = p >= 0 ? "profit" : "loss";
  return (
    `${TRADE_ALERT_PREFIX_A.trimEnd()}${marker}BROKER A #${trade.id}: closed ${trade.trade_type} 1 oz XAU/USD @ $${price.toFixed(2)} ` +
    `(opened @ $${trade.entry_price.toFixed(2)}, rule ${trade.rule_name}) -- ${result} of $${Math.abs(p).toFixed(2)} ` +
    `(manual close via Telegram, not the automatic trailing stop)\n` +
    `Filled: ${formatTs(now)}`
  );
}

async function openBrokerB(sql, tradeType, apiKey) {
  const open = await sql`SELECT id FROM broker_b_trades WHERE status = 'Open' ORDER BY open_ts DESC LIMIT 1`;
  if (open.length > 0) {
    return `${TRADE_ALERT_PREFIX_B.trimEnd()}${REJECT_MARKER}BROKER B: cannot open ${tradeType} -- a trade is already open. Close it first.`;
  }
  const forecasts = await sql`SELECT id FROM ta_forecasts ORDER BY ts DESC LIMIT 1`;
  if (forecasts.length === 0) {
    return `${TRADE_ALERT_PREFIX_B.trimEnd()}${REJECT_MARKER}BROKER B: cannot open ${tradeType} -- no TA forecast exists yet to attribute the trade to.`;
  }
  const price = await fetchGoldPrice(apiKey);
  if (price === null) {
    return `${TRADE_ALERT_PREFIX_B.trimEnd()}${REJECT_MARKER}BROKER B: cannot open ${tradeType} -- gold spot price unavailable right now.`;
  }
  const now = new Date();
  const ruleName = `Telegram-${tradeType.toLowerCase()}`;
  const inserted = await sql`
    INSERT INTO broker_b_trades (rule_name, trade_type, entry_price, open_ts, triggering_alerts, ta_forecast_id, status)
    VALUES (${ruleName}, ${tradeType}, ${price}, ${now.toISOString()}, 'Manual (Telegram command)', ${forecasts[0].id}, 'Open')
    RETURNING id
  `;
  return (
    `${TRADE_ALERT_PREFIX_B}BROKER B #${inserted[0].id}: opened ${tradeType} 1 oz XAU/USD @ $${price.toFixed(2)} (rule ${ruleName}).\n` +
    `Trigger: Manual (Telegram command)\n` +
    `Filled: ${formatTs(now)}`
  );
}

async function closeBrokerB(sql, apiKey) {
  const rows = await sql`
    SELECT id, rule_name, trade_type, entry_price FROM broker_b_trades
    WHERE status = 'Open' ORDER BY open_ts DESC LIMIT 1
  `;
  if (rows.length === 0) {
    return `${TRADE_ALERT_PREFIX_B.trimEnd()}${REJECT_MARKER}BROKER B: no open trade to close.`;
  }
  const trade = rows[0];
  const price = await fetchGoldPrice(apiKey);
  if (price === null) {
    return `${TRADE_ALERT_PREFIX_B.trimEnd()}${REJECT_MARKER}BROKER B: cannot close -- gold spot price unavailable right now.`;
  }
  const now = new Date();
  const p = pnl(trade.trade_type, trade.entry_price, price);
  await sql`
    UPDATE broker_b_trades SET exit_price = ${price}, close_ts = ${now.toISOString()}, pnl = ${p}, status = 'Closed'
    WHERE id = ${trade.id}
  `;
  const marker = p >= 0 ? PROFIT_MARKER_B : LOSS_MARKER_B;
  const result = p >= 0 ? "profit" : "loss";
  return (
    `${TRADE_ALERT_PREFIX_B.trimEnd()}${marker}BROKER B #${trade.id}: closed ${trade.trade_type} 1 oz XAU/USD @ $${price.toFixed(2)} ` +
    `(opened @ $${trade.entry_price.toFixed(2)}, rule ${trade.rule_name}) -- ${result} of $${Math.abs(p).toFixed(2)} ` +
    `(manual close via Telegram, not the automatic trailing stop)\n` +
    `Filled: ${formatTs(now)}`
  );
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("This is a Telegram webhook endpoint.", { status: 200 });
    }

    const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.TELEGRAM_WEBHOOK_SECRET || secretHeader !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("Bad Request", { status: 400 });
    }

    const message = update.message;
    if (!message || typeof message.text !== "string") {
      return new Response("OK", { status: 200 }); // not a text message -- nothing to do
    }
    if (String(message.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) {
      return new Response("OK", { status: 200 }); // unauthorized chat -- silently ignore
    }

    if (BRA_RE.test(message.text)) {
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, await runBra(env));
      return new Response("OK", { status: 200 });
    }

    if (SLA_RE.test(message.text)) {
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, await runSla(env));
      return new Response("OK", { status: 200 });
    }

    if (RUN_TA_RE.test(message.text)) {
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, await runTa(env));
      return new Response("OK", { status: 200 });
    }

    const sql = neon(env.DATABASE_URL);

    if (REARM_RE.test(message.text)) {
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, await rearmLevels(sql));
      return new Response("OK", { status: 200 });
    }

    const trail = parseTrail(message.text);
    if (trail) {
      const trailReply = trail.error ? `${REJECT_MARKER}${trail.error}` : await setTrail(sql, trail.activation, trail.distance);
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, trailReply);
      return new Response("OK", { status: 200 });
    }

    const stopLoss = parseStopLoss(message.text);
    if (stopLoss) {
      const slReply = stopLoss.error ? `${REJECT_MARKER}${stopLoss.error}` : await setStopLoss(sql, stopLoss.value);
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, slReply);
      return new Response("OK", { status: 200 });
    }

    const control = parseControlCommand(message.text);
    if (control) {
      let controlReply;
      if (control.action === "stop") controlReply = await stopTrading(sql, env.TWELVE_DATA_API_KEY, env);
      else if (control.action === "start") controlReply = await startTrading(sql);
      else if (control.action === "pause") controlReply = await schedulePause(sql, control.start, control.end);
      else controlReply = `${REJECT_MARKER}${control.message}`;
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, controlReply);
      return new Response("OK", { status: 200 });
    }

    const parsed = parseCommand(message.text);
    if (!parsed) {
      return new Response("OK", { status: 200 }); // not a recognized command -- no reply
    }

    let reply;
    if (parsed.brokerLetter === "F") {
      reply = await queueBrokerF(sql, env, parsed.action === "open" ? parsed.tradeType.toLowerCase() : "close");
    } else if (parsed.action === "open") {
      reply =
        parsed.brokerLetter === "A"
          ? await openBrokerA(sql, parsed.tradeType, env.TWELVE_DATA_API_KEY)
          : await openBrokerB(sql, parsed.tradeType, env.TWELVE_DATA_API_KEY);
    } else {
      reply =
        parsed.brokerLetter === "A"
          ? await closeBrokerA(sql, env.TWELVE_DATA_API_KEY)
          : await closeBrokerB(sql, env.TWELVE_DATA_API_KEY);
    }
    await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, reply);

    return new Response("OK", { status: 200 });
  },
};

export { REARM_RE, RUN_TA_RE, SLA_RE, BRA_RE, parseStopLoss, parseTrail, parseCommand, parseControlCommand, etToUtc, formatTs, pnl }; // exported for the test file only
