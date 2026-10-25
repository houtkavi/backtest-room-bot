"""Backtest Room - pure calculation engine (no Telegram code here).

Balances are tracked as P&L relative to the starting balance (start = 0).

Intraday trailing (Apex style):
    threshold = highest closed P&L so far - drawdown
    It stops moving once it reaches LOCK_AT (start balance + 100, like Apex).
EOD trailing:
    threshold only moves at the END of a trading day, using that day's
    closing P&L. During the day it stays fixed.
    A day "ends" when you log a trade on a different day than the last trade.

Blown: P&L <= threshold.   Passed: P&L >= target.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

LOCK_AT = 100.0  # threshold stops trailing at start balance + $100 (Apex rule)
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


@dataclass
class Trade:
    n: int
    day: str
    week: int
    result: str  # "TP" or "SL"
    pnl: float
    balance: float  # P&L after this trade
    threshold: float  # threshold after this trade
    room: float  # distance to threshold after this trade


@dataclass
class Backtest:
    mode: str  # "INTRADAY" or "EOD"
    target: float
    drawdown: float
    sl: float
    tp: float
    rr: float
    balance: float = 0.0
    peak: float = 0.0
    threshold: float = 0.0
    status: str = "ACTIVE"  # ACTIVE / PASSED / BLOWN
    week: int = 1
    last_day: str | None = None
    trades: list = field(default_factory=list)

    def __post_init__(self):
        if not self.trades and self.balance == 0 and self.threshold == 0:
            self.threshold = -self.drawdown

    # ---------- core ----------
    def _trail_to(self, level: float):
        new_thr = min(level - self.drawdown, LOCK_AT)
        if new_thr > self.threshold:
            self.threshold = new_thr

    def _close_day(self):
        """EOD: trail threshold from the closing balance of the day that ended."""
        if self.mode == "EOD":
            self._trail_to(self.balance)

    def log(self, day: str, result: str) -> Trade:
        if self.status != "ACTIVE":
            raise ValueError("Backtest is finished. Restart to begin a new one.")
        if day not in DAYS or result not in ("TP", "SL"):
            raise ValueError("bad input")

        # new day? (and new week if the day went backwards, e.g. Fri -> Mon)
        if self.last_day is not None and day != self.last_day:
            self._close_day()
            if DAYS.index(day) < DAYS.index(self.last_day):
                self.week += 1
        self.last_day = day

        pnl = self.tp if result == "TP" else -self.sl
        self.balance += pnl
        self.peak = max(self.peak, self.balance)

        if self.mode == "INTRADAY":
            self._trail_to(self.balance)

        if self.balance <= self.threshold:
            self.status = "BLOWN"
        elif self.balance >= self.target:
            self.status = "PASSED"

        t = Trade(
            n=len(self.trades) + 1, day=day, week=self.week, result=result,
            pnl=pnl, balance=self.balance, threshold=self.threshold,
            room=self.balance - self.threshold,
        )
        self.trades.append(t)
        return t

    def undo(self) -> bool:
        """Remove the last trade by replaying everything before it."""
        if not self.trades:
            return False
        keep = [(t.day, t.result) for t in self.trades[:-1]]
        fresh = Backtest(self.mode, self.target, self.drawdown, self.sl, self.tp, self.rr)
        for d, r in keep:
            fresh.log(d, r)
        self.__dict__.update(fresh.__dict__)
        return True

    # ---------- stats ----------
    def stats(self) -> dict:
        tr = self.trades
        wins = [t for t in tr if t.result == "TP"]
        losses = [t for t in tr if t.result == "SL"]
        # max drawdown from peak (closed balance)
        peak, max_dd = 0.0, 0.0
        for t in tr:
            peak = max(peak, t.balance)
            max_dd = max(max_dd, peak - t.balance)
        # streaks
        best_w = best_l = cur = 0
        prev = None
        for t in tr:
            cur = cur + 1 if t.result == prev else 1
            prev = t.result
            if t.result == "TP":
                best_w = max(best_w, cur)
            else:
                best_l = max(best_l, cur)
        gross_w = sum(t.pnl for t in wins)
        gross_l = -sum(t.pnl for t in losses)
        by_day = {}
        for d in DAYS:
            dt = [t for t in tr if t.day == d]
            if dt:
                w = sum(1 for t in dt if t.result == "TP")
                by_day[d] = {"trades": len(dt), "wins": w, "losses": len(dt) - w,
                             "pnl": sum(t.pnl for t in dt)}
        return {
            "trades": len(tr), "wins": len(wins), "losses": len(losses),
            "win_rate": (len(wins) / len(tr) * 100) if tr else 0.0,
            "net": self.balance, "gross_win": gross_w, "gross_loss": gross_l,
            "profit_factor": (gross_w / gross_l) if gross_l else None,
            "expectancy": (self.balance / len(tr)) if tr else 0.0,
            "max_dd": max_dd, "peak": self.peak,
            "best_win_streak": best_w, "worst_loss_streak": best_l,
            "to_target": max(self.target - self.balance, 0),
            "room": self.balance - self.threshold,
            "by_day": by_day, "weeks": self.week,
        }

    # ---------- save / load ----------
    def to_dict(self):
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d):
        trades = [Trade(**t) for t in d.pop("trades", [])]
        b = cls(**d)
        b.trades = trades
        return b
