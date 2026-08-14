"""Per-scanner mail presence — replaces the 06:00 health check's bare count.

Addendum A1, 2026-08-05.

WHAT WAS WRONG, MEASURED RATHER THAN GUESSED
--------------------------------------------
The 06:00 email said ``Only 2 scanner email(s) in 48h — check PA tasks``. The check
counted subjects against a keyword list::

    ["8-K", "PEAD Scanner", "SI SQUEEZE", "COT",
     "CEL Scanner", "Crypto Scanner", "Dividend Scanner"]

Every one of those matches a scanner's QUIET-DAY subject and none matches its
SIGNAL-DAY subject. `PEAD Scanner -- No signal` matches; `PEAD BULL: HY:3, ...`
does not. `CEL Scanner -- No signal` matches; `CEL BEAR: XOP, ...` does not.
`Form 4 Scanner Status`, `Cross-Signal Scanner` and `Dividend Cut ALERT` match
nothing at all — the list never contained a pattern for them.

**So the check reads greenest when the scanners find nothing and goes red when they
find something.** That is not a threshold that needs tuning; it is a question asked
backwards.

WHY A COUNT COULD NOT HAVE BEEN FIXED, ONLY REPLACED
-----------------------------------------------------
Three scanners mailing twice and seven mailing zero times both total six. A count
cannot name the scanner that went quiet, which is the only fact that lets anyone
act — and the runbook line it printed, "check PA tasks", is the count admitting it
does not know which task to check. Each scanner now gets its own presence check in
its own window, and a failure NAMES it.

THE CASE WHERE SILENCE IS CORRECT
----------------------------------
13F runs daily and mails only inside a filing window (45 days after each quarter
end) — read from ``thirteenf_scanner.py``'s own header, not inferred from the fact
that it is scheduled daily. Outside that window its silence is the design working,
and grading it MISSING would manufacture a daily failure for a healthy scanner —
the `pa_watch` item 3-5 defect. It is UNJUDGEABLE from mail there and says so.

**And the rail that stops UNJUDGEABLE becoming a way to go quiet forever:** a run in
which nothing was judgeable is not a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

PRESENT = "PRESENT"
MISSING = "MISSING"
UNJUDGEABLE = "UNJUDGEABLE"

_DAILY = 26     # hours — one calendar day plus slack, the estate's daily contract


@dataclass(frozen=True)
class ScannerMailSpec:
    """One scanner's mail contract.

    ``patterns`` must cover BOTH subject forms. A scanner recognised only when it is
    quiet is a scanner the check loses the moment it has something to say, which is
    exactly the defect this module replaces — so ``examples`` carries one real
    subject of each form and a test asserts every pattern set recognises its own.
    """
    name: str
    patterns: Tuple[str, ...]
    examples: Tuple[str, ...] = ()
    window_hours: int = _DAILY
    emails_conditionally: bool = False
    note: str = ""

    def matches(self, subject: str) -> bool:
        s = (subject or "").lower()
        return any(p.lower() in s for p in self.patterns)


#: Built from the subjects these scanners ACTUALLY send, read out of the mailbox on
#: 2026-08-05 rather than from anyone's memory of what they are called.
REGISTRY: Dict[str, ScannerMailSpec] = {
    "form4": ScannerMailSpec(
        "form4", ("Form 4 Scanner Status", "F4 CROSS", "Form 4 Status"),
        examples=("Form 4 Scanner Status - 2026-08-04",)),
    "cross_signal": ScannerMailSpec(
        "cross_signal", ("Cross-Signal Scanner",),
        examples=("\U0001f4cb Cross-Signal Scanner: No Tier2 signals today",)),
    "dividend_cut": ScannerMailSpec(
        "dividend_cut", ("Dividend Cut", "DIVIDEND CUT SCANNER"),
        examples=("\U0001f7e2 Dividend Cut ALERT: UCPLF — BUY Signal",)),
    "dividend_initiation": ScannerMailSpec(
        "dividend_initiation", ("DIV INITIATION", "Dividend Initiation Scanner"),
        examples=("DIV INITIATION: 1 First-Ever | T1: SPHRY",
                  "DIV INITIATION: No new initiations detected")),
    "pead": ScannerMailSpec(
        "pead", ("PEAD Scanner", "PEAD BULL", "PEAD BEAR"),
        examples=("PEAD Scanner -- No signal (2026-08-03)",
                  "PEAD BULL: HY:3, TALO:3, CTRI:3")),
    "cel": ScannerMailSpec(
        "cel", ("CEL Scanner", "CEL BEAR", "CEL BULL"),
        examples=("CEL Scanner -- No signal (2026-08-03)",
                  "CEL BEAR: XOP, XLE, CVX, XOM, COP")),
    "eight_k": ScannerMailSpec(
        "eight_k", ("8-K",),
        examples=("8-K SHORT: GVA, WCN, PNRG, EA, PWR",)),
    "thirteenf": ScannerMailSpec(
        "thirteenf", ("13F", "THIRTEENF"),
        examples=("13F CLUSTER BUY: ACME, BETA",),
        emails_conditionally=True,
        note="runs daily, mails only inside a 13F filing window (45 days after each "
             "quarter end) — verified in thirteenf_scanner.py's header. Outside the "
             "window its silence is the design, not a failure."),
}

#: Scanners deliberately NOT registered, each with the reason. An unexplained
#: omission is how a dead scanner becomes invisible; a named one is a decision.
NOT_REGISTERED = {
    "si_squeeze": "last signal_log scan_date 2026-07-15 — cadence is not established "
                  "from the box, and asserting 'daily' would invent a contract. "
                  "Registering it on a guess would page daily about a scanner that "
                  "may be correctly retired.",
    "cot": "last signal_log scan_date 2026-07-28 and the CFTC source is weekly — a "
           "daily contract would read overdue six days in seven, which is the "
           "pa_watch item 3-5 defect.",
    "crypto": "in the old keyword list and absent from the mailbox entirely; whether "
              "it still exists is not established from this box.",
}


@dataclass
class ScannerMailReport:
    states: Dict[str, str] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    ok: bool = False

    def to_dict(self) -> dict:
        return {"ok": self.ok, "states": dict(self.states),
                "missing": list(self.missing), "problems": list(self.problems),
                "notes": list(self.notes)}


def classify(subjects: Optional[Sequence[Tuple[datetime, str]]], *, now: datetime,
             registry: Optional[Mapping[str, ScannerMailSpec]] = None) -> ScannerMailReport:
    """Judge every registered scanner separately. ``subjects`` is ``(when, subject)``.

    ``None`` means the mailbox could not be READ, which is a different fact from an
    empty mailbox and must never collapse into it: zero emails is indistinguishable
    from every scanner being dead, and reporting that when the truth is "IMAP failed"
    sends the operator to the wrong estate entirely.
    """
    reg = REGISTRY if registry is None else registry
    r = ScannerMailReport()

    if subjects is None:
        r.problems.append("the mailbox could not be read — no scanner can be judged, "
                          "which is NOT the same as no scanner having mailed")
        r.states = {name: UNJUDGEABLE for name in reg}
        r.ok = False
        return r

    rows = list(subjects)
    judged = 0
    for name, spec in sorted(reg.items()):
        cutoff = now - timedelta(hours=spec.window_hours)
        seen = any(when >= cutoff and spec.matches(subject) for when, subject in rows)
        if seen:
            r.states[name] = PRESENT
            judged += 1
            continue
        if spec.emails_conditionally:
            r.states[name] = UNJUDGEABLE
            r.notes.append(
                f"{name}: no mail in {spec.window_hours}h, and none is owed — this "
                f"scanner mails only inside its own event window. {spec.note}")
            continue
        r.states[name] = MISSING
        r.missing.append(name)
        judged += 1
        r.problems.append(
            f"{name}: no scanner email in the last {spec.window_hours}h "
            f"(expected subject like {spec.examples[0]!r})" if spec.examples else
            f"{name}: no scanner email in the last {spec.window_hours}h")

    if judged == 0:
        # Every scanner conditional and quiet. Green here would mean the check can
        # report success having established nothing at all (A6).
        r.problems.append("nothing was judgeable — every registered scanner is "
                          "conditional and none mailed, so this run proves nothing")

    r.ok = not r.problems
    return r


def summary_line(report: ScannerMailReport) -> str:
    """The one line the health email prints. It NAMES scanners.

    The old line was ``Only 2 scanner email(s) in 48h — check PA tasks``: a number
    that cannot be acted on, followed by a runbook step that is the check admitting
    it does not know which task to check.
    """
    if report.ok:
        n = sum(1 for s in report.states.values() if s == PRESENT)
        unj = [k for k, v in report.states.items() if v == UNJUDGEABLE]
        tail = f"; {', '.join(sorted(unj))} not due" if unj else ""
        return f"all {n} due scanner(s) reported{tail}"
    if report.missing:
        return f"NO EMAIL from: {', '.join(sorted(report.missing))}"
    return report.problems[0]
