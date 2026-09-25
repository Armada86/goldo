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
 * Scope, same as originally agreed: Broker A and Broker B only. The Forex broker
 * (forex_broker.py -- real orders on a FOREX.com demo account) is NOT reachable from here; its own
 * module docstring says its full trading logic must stay disconnected from automation unless
 * explicitly asked to connect it, and this webhook was never asked to include it.
 *
 * Commands recognized in an incoming Telegram message's text (case-insensitive, forgiving about
 * word order/spacing):
 *   "buy broker a" / "sell broker B" / "Broker A buy" -> open a Buy/Sell position on that broker
 *   "close broker a" / "close broker B"                -> close that broker's open position NOW,
 *                                                          at the current spot price, regardless
 *                                                          of unrealized P/L (unlike the automatic
 *                                                          $10 take-profit/stop-loss, a manual
 *                                                          close is an unconditional override)
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

const EXIT_THRESHOLD = 10.0; // must match broker.EXIT_THRESHOLD / broker_b's same constant

const TRADE_ALERT_PREFIX_A = "\u{1F535} "; // blue circle -- broker.TRADE_ALERT_PREFIX
const PROFIT_MARKER_A = "\u{1F7E2} "; // green circle -- broker.PROFIT_MARKER
const LOSS_MARKER_A = "\u{1F534} "; // red circle -- broker.LOSS_MARKER

const TRADE_ALERT_PREFIX_B = "\u{1F7E6} "; // blue square -- broker_b.TRADE_ALERT_PREFIX
const PROFIT_MARKER_B = "\u{1F7E9} "; // green square -- broker_b.PROFIT_MARKER
const LOSS_MARKER_B = "\u{1F7E5} "; // red square -- broker_b.LOSS_MARKER

const REJECT_MARKER = "⛔ "; // no-entry sign -- same as broker.BLOCKED_MARKER/broker_b.BLOCKED_MARKER

const DIRECTION_RE = /\b(buy|sell)\b/i;
const BROKER_RE = /\bbroker\s+([ab])\b/i;
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
  await sql`
    INSERT INTO trades (rule_name, trade_type, entry_price, open_ts, triggering_alerts, status)
    VALUES (${ruleName}, ${tradeType}, ${price}, ${now.toISOString()}, 'Manual (Telegram command)', 'Open')
  `;
  return (
    `${TRADE_ALERT_PREFIX_A}BROKER A: opened ${tradeType} 1 oz XAU/USD @ $${price.toFixed(2)} (rule ${ruleName}).\n` +
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
    `${TRADE_ALERT_PREFIX_A.trimEnd()}${marker}BROKER A: closed ${trade.trade_type} 1 oz XAU/USD @ $${price.toFixed(2)} ` +
    `(opened @ $${trade.entry_price.toFixed(2)}, rule ${trade.rule_name}) -- ${result} of $${Math.abs(p).toFixed(2)} ` +
    `(manual close via Telegram, not the $${EXIT_THRESHOLD.toFixed(0)} auto-target)\n` +
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
  await sql`
    INSERT INTO broker_b_trades (rule_name, trade_type, entry_price, open_ts, triggering_alerts, ta_forecast_id, status)
    VALUES (${ruleName}, ${tradeType}, ${price}, ${now.toISOString()}, 'Manual (Telegram command)', ${forecasts[0].id}, 'Open')
  `;
  return (
    `${TRADE_ALERT_PREFIX_B}BROKER B: opened ${tradeType} 1 oz XAU/USD @ $${price.toFixed(2)} (rule ${ruleName}).\n` +
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
    `${TRADE_ALERT_PREFIX_B.trimEnd()}${marker}BROKER B: closed ${trade.trade_type} 1 oz XAU/USD @ $${price.toFixed(2)} ` +
    `(opened @ $${trade.entry_price.toFixed(2)}, rule ${trade.rule_name}) -- ${result} of $${Math.abs(p).toFixed(2)} ` +
    `(manual close via Telegram, not the $${EXIT_THRESHOLD.toFixed(0)} auto-target)\n` +
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

    const parsed = parseCommand(message.text);
    if (!parsed) {
      return new Response("OK", { status: 200 }); // not a recognized command -- no reply
    }

    const sql = neon(env.DATABASE_URL);
    let reply;
    if (parsed.action === "open") {
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

export { parseCommand, formatTs, pnl }; // exported for the test file only
