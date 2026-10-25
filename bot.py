"""The Backtest Room - Telegram bot (Beta v1.0)

Commands:  /start  /restart  /print
Run:       BOT_TOKEN=xxxx python bot.py
"""

from __future__ import annotations

import io
import csv
import json
import os
import logging
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

from engine import Backtest, DAYS, LOCK_AT

VERSION = "Beta v1.0"
DATA_FILE = Path(os.environ.get("DATA_FILE", "data/backtests.json"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler("bot_log.txt", mode="w", encoding="utf-8")])
log = logging.getLogger("backtest-room")
logging.getLogger("httpx").setLevel(logging.WARNING)


class _Redact(logging.Filter):
    """Never write the bot token into logs."""
    import re as _re
    _pat = _re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")

    def filter(self, record):
        record.msg = self._pat.sub("<TOKEN>", str(record.getMessage()))
        record.args = ()
        if record.exc_info:
            import traceback
            txt = "".join(traceback.format_exception(*record.exc_info))
            record.msg += "\n" + self._pat.sub("<TOKEN>", txt)
            record.exc_info = None
            record.exc_text = None
        return True


for _h in logging.getLogger().handlers:
    _h.addFilter(_Redact())

# setup steps, in order (after the Intraday/EOD button)
STEPS = [
    ("target",   "🎯 *Profit target?*\nType the amount, e.g. `2500`, `3000`, `5000`"),
    ("drawdown", "🛡 *Drawdown room?*\nType the trailing drawdown, e.g. `2000`, `2500`, `3000`"),
    ("sl",       "🔻 *SL size (per R)?*\nType the loss per trade, e.g. `200`, `300`, `350`, `500`"),
    ("tp",       "🔺 *TP size?*\nType the profit per winning trade, e.g. `300`, `500`, `1000`"),
    ("rr",       "⚖️ *RR?*\nType it, e.g. `1.0`, `1.2`, `1.5`, `1.8`, `3`"),
]
MODE_NAME = {"INTRADAY": "Intraday trailing", "EOD": "EOD trailing"}

# ---------------- storage ----------------
# per chat: {"setup": {...} or None, "step": int or None, "bt": dict or None}
DB: dict = {}


def load_db():
    global DB
    if DATA_FILE.exists():
        try:
            DB = json.loads(DATA_FILE.read_text())
        except Exception:
            log.exception("could not read data file, starting empty")
            DB = {}


def save_db():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DB, indent=1))
    tmp.replace(DATA_FILE)


def chat(cid) -> dict:
    return DB.setdefault(str(cid), {"setup": None, "step": None, "bt": None})


def get_bt(cid) -> Backtest | None:
    d = chat(cid).get("bt")
    return Backtest.from_dict(dict(d)) if d else None


def put_bt(cid, bt: Backtest | None):
    chat(cid)["bt"] = bt.to_dict() if bt else None
    save_db()


# ---------------- helpers ----------------
def money(x: float) -> str:
    s = f"{abs(x):,.2f}".rstrip("0").rstrip(".")
    return f"-${s}" if x < 0 else f"${s}"


def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in r] for r in rows])


KB_MODE = kb([[("📈 Intraday", "mode:INTRADAY"), ("🌙 EOD", "mode:EOD")]])
KB_CONFIRM = kb([[("✅ OK", "confirm:ok"), ("🔄 Restart", "confirm:restart")]])
KB_DAYS = kb([[(d, f"day:{d}") for d in DAYS], [("📊 Print", "print"), ("↩️ Undo last", "undo")]])
KB_END = kb([[("🔄 Restart", "restart"), ("📊 Print Performance", "print")]])


def kb_result(day):
    return kb([[("✅ TP", f"res:{day}:TP"), ("❌ SL", f"res:{day}:SL")], [("⬅️ Back", "back")]])


def status_line(bt: Backtest) -> str:
    return (f"💰 P&L: *{money(bt.balance)}*   🎯 to target: {money(max(bt.target - bt.balance, 0))}\n"
            f"🛡 Threshold: {money(bt.threshold)}   Room left: *{money(bt.balance - bt.threshold)}*")


# ---------------- setup flow ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = chat(update.effective_chat.id)
    c["setup"], c["step"], c["bt"] = {}, None, None
    save_db()
    await update.effective_chat.send_message(
        f"🧪 *The Backtest Room* — {VERSION}\n\nNew backtest. Which drawdown type?",
        parse_mode="Markdown", reply_markup=KB_MODE)


async def ask_step(update: Update, i: int):
    c = chat(update.effective_chat.id)
    c["step"] = i
    save_db()
    await update.effective_chat.send_message(STEPS[i][1], parse_mode="Markdown")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = chat(update.effective_chat.id)
    if c.get("step") is None or c.get("setup") is None:
        await update.message.reply_text("Send /start to begin a new backtest, or /print for your sheet.")
        return
    raw = update.message.text.strip().lower().replace(",", "").replace("$", "").replace("rr", "").strip()
    try:
        val = float(raw)
        if val <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please type a number bigger than 0 🙂")
        return
    key = STEPS[c["step"]][0]
    c["setup"][key] = val
    if c["step"] + 1 < len(STEPS):
        await ask_step(update, c["step"] + 1)
    else:
        c["step"] = None
        save_db()
        await send_confirm(update)


async def send_confirm(update: Update):
    s = chat(update.effective_chat.id)["setup"]
    real_rr = s["tp"] / s["sl"]
    note = ""
    if abs(real_rr - s["rr"]) > 0.01:
        note = (f"\n⚠️ TP ÷ SL = {real_rr:.2f}R, but you typed {s['rr']:g}R. "
                f"I'll pay wins at your TP size ({money(s['tp'])}). Hit Restart if that's wrong.")
    text = (
        "📋 *Please confirm your backtest*\n\n"
        f"Type: *{MODE_NAME[s['mode']]}*\n"
        f"Target: *{money(s['target'])}*\n"
        f"Drawdown: *{money(s['drawdown'])}*\n"
        f"SL per trade: *{money(s['sl'])}*\n"
        f"TP per trade: *{money(s['tp'])}*\n"
        f"RR: *{s['rr']:g}*\n\n"
        f"Losses before blow (from start): {int(s['drawdown'] // s['sl'])} SL in a row\n"
        f"Wins needed (no losses): {-(-s['target'] // s['tp']):.0f} TP"
        f"{note}"
    )
    await update.effective_chat.send_message(text, parse_mode="Markdown", reply_markup=KB_CONFIRM)


# ---------------- buttons ----------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cid = update.effective_chat.id
    c = chat(cid)
    data = q.data

    if data.startswith("mode:"):
        c["setup"] = {"mode": data.split(":")[1]}
        await q.edit_message_text(f"Drawdown type: *{MODE_NAME[c['setup']['mode']]}* ✅", parse_mode="Markdown")
        await ask_step(update, 0)
        return

    if data == "confirm:ok":
        s = c.get("setup") or {}
        need = {"mode", "target", "drawdown", "sl", "tp", "rr"}
        if not need <= s.keys():
            await q.edit_message_text("Setup is missing something. Send /start again.")
            return
        bt = Backtest(s["mode"], s["target"], s["drawdown"], s["sl"], s["tp"], s["rr"])
        c["setup"] = None
        put_bt(cid, bt)
        await q.edit_message_reply_markup(None)
        await q.message.reply_text(
            "🚀 *Backtest started!*\n" + status_line(bt) + "\n\nPick the day of your trade:",
            parse_mode="Markdown", reply_markup=KB_DAYS)
        return

    if data in ("confirm:restart", "restart"):
        await q.edit_message_reply_markup(None)
        await cmd_start(update, context)
        return

    if data == "print":
        await send_print(update)
        return

    bt = get_bt(cid)
    if bt is None:
        await q.message.reply_text("No active backtest. Send /start.")
        return

    if data.startswith("day:"):
        if bt.status != "ACTIVE":
            await q.message.reply_text("This backtest is finished.", reply_markup=KB_END)
            return
        day = data.split(":")[1]
        await q.edit_message_text(f"Trade #{len(bt.trades) + 1} — *{day}*. Result?",
                                  parse_mode="Markdown", reply_markup=kb_result(day))
        return

    if data == "back":
        await q.edit_message_text("Pick the day of your trade:", reply_markup=KB_DAYS)
        return

    if data == "undo":
        if bt.undo():
            put_bt(cid, bt)
            await q.edit_message_text("↩️ Last trade removed.\n\n" + status_line(bt) + "\n\nPick the day:",
                                      parse_mode="Markdown", reply_markup=KB_DAYS)
        else:
            await q.message.reply_text("Nothing to undo yet.")
        return

    if data.startswith("res:"):
        _, day, res = data.split(":")
        if bt.status != "ACTIVE":
            await q.message.reply_text("This backtest is finished.", reply_markup=KB_END)
            return
        t = bt.log(day, res)
        put_bt(cid, bt)
        icon = "✅" if res == "TP" else "❌"
        head = f"{icon} #{t.n} {day} (W{t.week}) {res} {money(t.pnl)}\n" + status_line(bt)
        await q.edit_message_text(head, parse_mode="Markdown")

        if bt.status == "BLOWN":
            await q.message.reply_text(
                f"💥 *ACCOUNT BLOWN* on trade #{t.n}.\n"
                f"P&L {money(bt.balance)} hit the threshold {money(bt.threshold)}.\n"
                f"{len(bt.trades)} trades, win rate {bt.stats()['win_rate']:.1f}%.",
                parse_mode="Markdown", reply_markup=KB_END)
        elif bt.status == "PASSED":
            await q.message.reply_text(
                f"🏆 *CONGRATS — YOU PASSED!* 🎉\n"
                f"Target {money(bt.target)} reached in {len(bt.trades)} trades "
                f"(win rate {bt.stats()['win_rate']:.1f}%).\nFinal P&L: *{money(bt.balance)}*",
                parse_mode="Markdown", reply_markup=KB_END)
        else:
            warn = ""
            if bt.balance - bt.threshold <= bt.sl:
                warn = "\n⚠️ *Danger:* one more SL blows the account."
            await q.message.reply_text("Next trade — pick the day:" + warn,
                                       parse_mode="Markdown", reply_markup=KB_DAYS)
        return


# ---------------- print ----------------
def build_sheet(bt: Backtest) -> str:
    s = bt.stats()
    pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "∞"
    status = {"ACTIVE": "🟡 In progress", "PASSED": "🟢 PASSED", "BLOWN": "🔴 BLOWN"}[bt.status]
    lines = [
        "📊 PERFORMANCE SHEET",
        f"Status: {status}",
        "",
        f"Type     : {MODE_NAME[bt.mode]}",
        f"Target   : {money(bt.target)}   Drawdown: {money(bt.drawdown)}",
        f"SL / TP  : {money(bt.sl)} / {money(bt.tp)}   RR: {bt.rr:g}",
        "",
        f"Trades   : {s['trades']}  (W {s['wins']} / L {s['losses']})",
        f"Win rate : {s['win_rate']:.1f}%",
        f"Net P&L  : {money(s['net'])}",
        f"Gross    : +{money(s['gross_win'])} / -{money(s['gross_loss'])}",
        f"Profit F.: {pf}",
        f"Avg/trade: {money(s['expectancy'])}",
        f"Peak P&L : {money(s['peak'])}",
        f"Max DD   : {money(s['max_dd'])}",
        f"Streaks  : best {s['best_win_streak']}W / worst {s['worst_loss_streak']}L",
        f"Threshold: {money(bt.threshold)}  (room {money(s['room'])})",
        f"To target: {money(s['to_target'])}",
        f"Weeks    : {s['weeks']}",
    ]
    if s["by_day"]:
        lines += ["", "BY DAY", "Day  Trd  W  L   Win%      P&L"]
        for d, v in s["by_day"].items():
            wr = v["wins"] / v["trades"] * 100
            lines.append(f"{d}  {v['trades']:>3} {v['wins']:>2} {v['losses']:>2} {wr:>5.0f}% {money(v['pnl']):>9}")
    if bt.trades:
        lines += ["", "TRADE LOG", " #  Wk Day Res      P&L   Balance  Thresh"]
        for t in bt.trades[-40:]:
            lines.append(f"{t.n:>2}  W{t.week} {t.day} {t.result:<3} {money(t.pnl):>8} "
                         f"{money(t.balance):>9} {money(t.threshold):>7}")
        if len(bt.trades) > 40:
            lines.append(f"(last 40 of {len(bt.trades)} shown — full log in the CSV)")
    return "\n".join(lines)


def build_csv(bt: Backtest) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "Week", "Day", "Result", "PnL", "Balance", "Threshold", "Room"])
    for t in bt.trades:
        w.writerow([t.n, t.week, t.day, t.result, t.pnl, t.balance, t.threshold, t.room])
    return buf.getvalue().encode()


async def send_print(update: Update):
    bt = get_bt(update.effective_chat.id)
    if bt is None:
        await update.effective_chat.send_message("No backtest yet. Send /start.")
        return
    await update.effective_chat.send_message(f"```\n{build_sheet(bt)}\n```", parse_mode="Markdown")
    if bt.trades:
        await update.effective_chat.send_document(
            document=build_csv(bt), filename="backtest_trades.csv", caption="Full trade log (opens in Excel)")
    if bt.status != "ACTIVE":
        await update.effective_chat.send_message("What next?", reply_markup=KB_END)


async def cmd_print(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_print(update)


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "New backtest"),
        BotCommand("restart", "Throw away this backtest and start over"),
        BotCommand("print", "Print performance sheet"),
    ])


def main():
    import re
    m = re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", os.environ.get("BOT_TOKEN", ""))
    token = m.group(0) if m else None
    if not token:
        raise SystemExit("No valid bot token found. Delete token.txt and run START_BOT.bat again.")
    load_db()
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("restart", cmd_start))
    app.add_handler(CommandHandler("print", cmd_print))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Backtest Room %s running", VERSION)
    app.run_polling()


if __name__ == "__main__":
    import sys, platform
    log.info("Python %s", platform.python_version())
    try:
        main()
    except BaseException:
        log.exception("BOT CRASHED")
        raise
