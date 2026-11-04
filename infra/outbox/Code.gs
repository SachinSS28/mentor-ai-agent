/**
 * Mentor's outbox: a Google Apps Script in the owner's own Google account that sends Mentor's
 * planned Telegram messages (task reminders, the morning plan) at their time, even while the
 * Mac is off. Mentor, when the Mac is on, hands over its plan (`mentor outbox`); a trigger
 * runs `deliver` every minute.
 *
 * Setup (once): `mentor outbox --setup` creates and deploys it through the Apps Script API and
 * opens it once for the owner to approve; by hand: paste this file into a new project at
 * script.google.com, Deploy > New deployment > Web app (Execute as: Me; Who has access: Anyone),
 * and give the web app's address to `mentor outbox --connect`. Mentor then sends the bot's key and
 * a secret of its own; after that, nothing without the secret is accepted.
 *
 * It uses only script properties, a time trigger and requests to api.telegram.org: no access to
 * Drive, Gmail or anything else in the account.
 */

var PROPS = PropertiesService.getScriptProperties();
var MAX_MESSAGES = 100; // each is a few KB; script properties hold 500 KB in all
var KEEP_SENT = 250; // about 6 KB, under the 9 KB limit for one property
var TOO_OLD_MS = 36 * 3600 * 1000; // a message more than 36 hours late is dropped, not sent

/** Opening the web app's address once, as the owner, is how the script gets approved. */
function doGet() {
  return ContentService.createTextOutput(
    "Mentor's outbox is ready. You can close this tab and go back to Mentor."
  );
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    var stored = PROPS.getProperty("SECRET");
    if (body.action === "connect") {
      if (stored && stored !== body.secret) {
        return reply({ ok: false, error: "This outbox is already connected to another Mentor." });
      }
      if (!body.secret || String(body.secret).length < 32 || !body.token || !body.chat_id) {
        return reply({ ok: false, error: "The secret, bot key and chat are all needed." });
      }
      PROPS.setProperties({
        SECRET: String(body.secret),
        BOT_TOKEN: String(body.token),
        CHAT_ID: String(body.chat_id),
      });
      ensureTrigger();
      return reply({ ok: true });
    }
    if (!stored || body.secret !== stored) {
      return reply({ ok: false, error: "Not allowed." });
    }
    if (body.action === "plan") {
      var sent = sentIds();
      PROPS.getKeys().forEach(function (key) {
        if (key.indexOf("msg:") === 0) PROPS.deleteProperty(key);
      });
      var add = {};
      (body.messages || []).slice(0, MAX_MESSAGES).forEach(function (m) {
        var id = String(m.id || "");
        if (!id || sent.indexOf(id) >= 0) return; // already delivered
        add["msg:" + id] = JSON.stringify({ at: Number(m.at), text: String(m.text).slice(0, 4000) });
      });
      PROPS.setProperties(add);
      ensureTrigger();
      return reply({ ok: true, pending: Object.keys(add).length, sent: sent });
    }
    if (body.action === "status") {
      var pending = PROPS.getKeys().filter(function (key) { return key.indexOf("msg:") === 0; });
      return reply({ ok: true, pending: pending.length, sent: sentIds() });
    }
    return reply({ ok: false, error: "Unknown action." });
  } finally {
    lock.releaseLock();
  }
}

/** Runs every minute: sends what's due, oldest first. */
function deliver() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    var token = PROPS.getProperty("BOT_TOKEN");
    var chat = PROPS.getProperty("CHAT_ID");
    if (!token || !chat) return;
    var now = Date.now();
    var all = PROPS.getProperties();
    var due = Object.keys(all)
      .filter(function (key) { return key.indexOf("msg:") === 0; })
      .map(function (key) { return { key: key, message: JSON.parse(all[key]) }; })
      .filter(function (item) { return item.message.at <= now; })
      .sort(function (a, b) { return a.message.at - b.message.at; });
    due.forEach(function (item) {
      var id = item.key.slice(4);
      if (now - item.message.at > TOO_OLD_MS) {
        PROPS.deleteProperty(item.key);
        return;
      }
      var code = send(token, chat, item.message.text, "HTML");
      if (code === 400) code = send(token, chat, plain(item.message.text), null); // formatting refused
      if (code >= 200 && code < 300) {
        PROPS.deleteProperty(item.key);
        rememberSent(id);
      } else if (code >= 400 && code < 500 && code !== 429) {
        PROPS.deleteProperty(item.key); // Telegram won't take it; don't retry forever
      }
    });
  } finally {
    lock.releaseLock();
  }
}

function send(token, chat, text, mode) {
  var payload = { chat_id: chat, text: text };
  if (mode) payload.parse_mode = mode;
  var response = UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/sendMessage", {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
  return response.getResponseCode();
}

function plain(html) {
  return html.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function sentIds() {
  return JSON.parse(PROPS.getProperty("SENT") || "[]");
}

function rememberSent(id) {
  var sent = sentIds();
  sent.push(id);
  PROPS.setProperty("SENT", JSON.stringify(sent.slice(-KEEP_SENT)));
}

function ensureTrigger() {
  var exists = ScriptApp.getProjectTriggers().some(function (trigger) {
    return trigger.getHandlerFunction() === "deliver";
  });
  if (!exists) ScriptApp.newTrigger("deliver").timeBased().everyMinutes(1).create();
}

function reply(object) {
  return ContentService.createTextOutput(JSON.stringify(object)).setMimeType(
    ContentService.MimeType.JSON
  );
}
