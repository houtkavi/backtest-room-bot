"""Restaurant-receipt style PDF for a backtest (80 mm thermal-paper look)."""

from __future__ import annotations

import io
from datetime import datetime, timezone, timedelta

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from engine import Backtest, DAYS

W = 80 * mm                 # receipt width
MARGIN = 5 * mm
COLS = 36                   # characters per line (Courier 9pt fits 70 mm)
FONT, BOLD, SIZE, LEAD = "Courier", "Courier-Bold", 9, 11.5
TZ = timezone(timedelta(hours=7))  # Phnom Penh
MODE = {"INTRADAY": "INTRADAY TRAILING", "EOD": "EOD TRAILING"}


def m(x: float) -> str:
    s = f"{abs(x):,.2f}"
    return ("-$" if x < 0 else "$") + s


def lr(left: str, right: str) -> str:
    gap = COLS - len(left) - len(right)
    return left + " " * max(gap, 1) + right


def build_lines(bt: Backtest, receipt_no: int, trader: str = "") -> list:
    """Each line: (text, style) where style in normal/bold/big/center/rule/dots/space."""
    s = bt.stats()
    now = datetime.now(TZ)
    L = []
    add = lambda t="", st="normal": L.append((t, st))

    add("THE BACKTEST ROOM", "big")
    add("Beta v1.0 - Trading Journal", "center")
    add("t.me/soley_backtest_room_bot", "center")
    add(st="space")
    add(lr(f"Receipt #{receipt_no:05d}", now.strftime("%d %b %Y")))
    add(lr(f"Table: {MODE[bt.mode].split()[0]}", now.strftime("%H:%M")))
    if trader:
        name = "".join(ch for ch in trader if ord(ch) < 256)[:24] or "Trader"
        add(lr("Trader:", name))
    add(st="dots")

    add("ORDER", "bold")
    add(lr("Drawdown type", MODE[bt.mode].split()[0]))
    add(lr("Profit target", m(bt.target)))
    add(lr("Drawdown room", m(bt.drawdown)))
    add(lr("SL per trade", m(bt.sl)))
    add(lr("TP per trade", m(bt.tp)))
    add(lr("RR", f"{bt.rr:g}R"))
    add(st="dots")

    add(lr("QTY ITEM", "AMOUNT"), "bold")
    if not bt.trades:
        add("  (no trades yet)")
    week = None
    for t in bt.trades:
        if t.week != week:
            week = t.week
            add(f"-- WEEK {week} --", "center")
        add(lr(f" 1x #{t.n:<3} {t.day} {'TAKE PROFIT' if t.result == 'TP' else 'STOP LOSS  '}",
               ("+" if t.pnl > 0 else "") + m(t.pnl)))
        add(lr("        bal " + m(t.balance), "dd " + m(t.threshold)), "small")
    add(st="dots")

    add(lr(f"Wins     x{s['wins']}", "+" + m(s["gross_win"])))
    add(lr(f"Losses   x{s['losses']}", "-" + m(s["gross_loss"])))
    add(st="rule")
    add(lr("NET P&L", m(s["net"])), "bold")
    add(st="rule")

    pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "MAX"
    add(lr("Trades", str(s["trades"])))
    add(lr("Win rate", f"{s['win_rate']:.1f}%"))
    add(lr("Profit factor", pf))
    add(lr("Avg per trade", m(s["expectancy"])))
    add(lr("Peak P&L", m(s["peak"])))
    add(lr("Max drawdown", m(s["max_dd"])))
    add(lr("Best win streak", f"{s['best_win_streak']}W"))
    add(lr("Worst loss streak", f"{s['worst_loss_streak']}L"))
    add(lr("Threshold", m(bt.threshold)))
    add(lr("Room left", m(s["room"])))
    add(lr("To target", m(s["to_target"])))
    add(lr("Weeks traded", str(s["weeks"])))

    if s["by_day"]:
        add(st="dots")
        add(lr("BY DAY   TRD  W  L", "P&L"), "bold")
        for d in DAYS:
            v = s["by_day"].get(d)
            if v:
                add(lr(f"{d}      {v['trades']:>3} {v['wins']:>2} {v['losses']:>2}", m(v["pnl"])))

    add(st="dots")
    stamp = {"PASSED": "*** PASSED ***", "BLOWN": "*** BLOWN ***", "ACTIVE": "- IN PROGRESS -"}[bt.status]
    add(stamp, "big")
    msg = {"PASSED": "Congrats! Target reached.",
           "BLOWN": "Account blown. Reset and go again.",
           "ACTIVE": "Keep going. Trade the plan."}[bt.status]
    add(msg, "center")
    add(st="space")
    add("THANK YOU FOR TRADING", "center")
    add("Please come again", "center")
    add(st="space")
    add("|| ||| | |||| || | ||| || |||| | ||", "center")
    add(f"BT-{now.strftime('%Y%m%d%H%M')}-{receipt_no:05d}", "center")
    return L


def _height(lines) -> float:
    h = 0
    for _, st in lines:
        h += {"big": LEAD * 1.6, "space": LEAD * 0.6, "dots": LEAD, "rule": LEAD * 0.5}.get(st, LEAD)
    return h


def render(bt: Backtest, receipt_no: int = 1, trader: str = "") -> bytes:
    lines = build_lines(bt, receipt_no, trader)
    # split into pages if very long (PDF page height limit)
    pages, cur, ch = [], [], 0
    for ln in lines:
        cur.append(ln)
        ch += LEAD
        if ch > 12000:
            pages.append(cur)
            cur, ch = [], 0
    if cur:
        pages.append(cur)

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle("Backtest Receipt")
    for pg in pages:
        H = _height(pg) + 2 * MARGIN + 4 * mm
        c.setPageSize((W, H))
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, W, H, stroke=0, fill=1)
        # zig-zag torn edges
        c.setFillColorRGB(0.85, 0.85, 0.85)
        for edge_y, up in ((H, False), (0, True)):
            p = c.beginPath()
            p.moveTo(0, edge_y)
            x, step = 0, 3 * mm
            while x < W:
                p.lineTo(x + step / 2, edge_y + (-1.5 * mm if not up else 1.5 * mm))
                p.lineTo(x + step, edge_y)
                x += step
            p.close()
            c.drawPath(p, stroke=0, fill=1)

        c.setFillColorRGB(0.1, 0.1, 0.1)
        y = H - MARGIN - 2 * mm
        cx = W / 2
        for text, st in pg:
            if st == "big":
                y -= LEAD * 1.6
                c.setFont(BOLD, 13)
                c.drawCentredString(cx, y + 3, text)
            elif st == "center":
                y -= LEAD
                c.setFont(FONT, SIZE)
                c.drawCentredString(cx, y, text)
            elif st == "space":
                y -= LEAD * 0.6
            elif st == "dots":
                y -= LEAD
                c.setFont(FONT, SIZE)
                c.drawString(MARGIN, y + 3, "-" * COLS)
            elif st == "rule":
                y -= LEAD * 0.5
                c.setLineWidth(0.8)
                c.line(MARGIN, y + 2, W - MARGIN, y + 2)
            else:
                y -= LEAD
                if st == "bold":
                    c.setFont(BOLD, SIZE)
                elif st == "small":
                    c.setFont(FONT, 7.5)
                    c.setFillColorRGB(0.4, 0.4, 0.4)
                else:
                    c.setFont(FONT, SIZE)
                c.drawString(MARGIN, y, text)
                c.setFillColorRGB(0.1, 0.1, 0.1)
        c.showPage()
    c.save()
    return buf.getvalue()
