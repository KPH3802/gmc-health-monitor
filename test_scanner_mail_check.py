#!/usr/bin/env python3
"""Tests for gmc_health.check_scanner_emails — the per-scanner presence check.

Stdlib unittest (no pytest dependency). Run: python3 -m unittest -v test_scanner_mail_check

WHAT THIS REPLACES, AND WHY THE OLD CHECK COULD NOT BE TUNED
------------------------------------------------------------
The old CHECK 5 counted INBOX subjects over 48h against seven keywords and
graded ``count >= 3`` GREEN. Every keyword matched a scanner's QUIET-DAY
subject and none matched its SIGNAL-DAY subject:

    "PEAD Scanner -- No signal"   matches
    "PEAD BULL: HY:3, TALO:3"     does NOT

So the check read greenest when the scanners found nothing and went red when
they found something. On 2026-08-14 it emitted a false "1 issue found" while
all five scanners had run and emailed. That is not a threshold needing tuning;
it is a question asked backwards, and `test_signal_day_mailbox_is_green` below
is the specific regression.

The replacement asks each scanner separately, inside its own window, and NAMES
any scanner that is silent — because "N emails" cannot tell you which task to
check, which is what the old runbook line ("check PA tasks") was admitting.
"""

import datetime
import hashlib
import json
import os
import unittest
from unittest import mock

import gmc_health
from vendored import scanner_mail

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 8, 14, 6, 0, 0, tzinfo=UTC)


def ago(hours):
    return NOW - datetime.timedelta(hours=hours)


# Real subjects, quiet form — one per non-conditional registered scanner.
QUIET_DAY = [
    (ago(3), "Form 4 Scanner Status - 2026-08-13"),
    (ago(3), "\U0001f4cb Cross-Signal Scanner: No Tier2 signals today"),
    (ago(3), "Dividend Cut Scanner: no cuts detected"),
    (ago(3), "DIV INITIATION: No new initiations detected"),
    (ago(3), "PEAD Scanner -- No signal (2026-08-13)"),
    (ago(3), "CEL Scanner -- No signal (2026-08-13)"),
    (ago(3), "8-K Scanner: no filings matched"),
]

# The SAME scanners, all having found something. The old check scored this
# WORSE than the quiet day; the new one must score it identically.
SIGNAL_DAY = [
    (ago(3), "F4 CROSS: 3 clusters — ACME, BETA, GAMMA"),
    (ago(3), "\U0001f4cb Cross-Signal Scanner: 2 Tier2 signals"),
    (ago(3), "\U0001f7e2 Dividend Cut ALERT: UCPLF — BUY Signal"),
    (ago(3), "DIV INITIATION: 1 First-Ever | T1: SPHRY"),
    (ago(3), "PEAD BULL: HY:3, TALO:3, CTRI:3"),
    (ago(3), "CEL BEAR: XOP, XLE, CVX, XOM, COP"),
    (ago(3), "8-K SHORT: GVA, WCN, PNRG, EA, PWR"),
]


class ScannerMailCheckTests(unittest.TestCase):

    # ---- the contract the WO names -------------------------------------- #

    def test_quiet_day_mailbox_is_green(self):
        """Scanners ran and found nothing. That is a healthy day."""
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=QUIET_DAY)
        self.assertEqual(status, gmc_health.GREEN, detail)

    def test_signal_day_mailbox_is_green(self):
        """THE 2026-08-14 REGRESSION. Every scanner ran AND found something.

        The old check graded this RED ("1 issue found") because its keywords
        only matched quiet-day subjects. Finding signals must never look like a
        failure of the machinery that found them.
        """
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=SIGNAL_DAY)
        self.assertEqual(status, gmc_health.GREEN, detail)

    def test_silent_scanner_is_red_and_is_NAMED(self):
        """A scanner that did not mail must be RED and must be named."""
        mailbox = [row for row in QUIET_DAY if "PEAD" not in row[1]]
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=mailbox)
        self.assertEqual(status, gmc_health.RED, detail)
        self.assertIn("pead", detail.lower())
        # and it must not name a scanner that DID report
        self.assertNotIn("cel", detail.lower())

    def test_two_silent_scanners_are_both_named(self):
        mailbox = [r for r in QUIET_DAY if "PEAD" not in r[1] and "CEL" not in r[1]]
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=mailbox)
        self.assertEqual(status, gmc_health.RED)
        self.assertIn("pead", detail.lower())
        self.assertIn("cel", detail.lower())

    # ---- anti-vacuous: the check must be able to fail -------------------- #

    def test_empty_mailbox_is_red_not_green(self):
        """Zero scanner emails is the loudest possible failure, not a pass."""
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=[])
        self.assertEqual(status, gmc_health.RED, detail)

    def test_unreadable_mailbox_is_not_confused_with_empty(self):
        """IMAP failure and an empty inbox are different facts.

        Reporting "no scanner mailed" when the truth is "we could not look"
        sends the operator to the wrong estate entirely.
        """
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=None)
        self.assertEqual(status, gmc_health.RED)
        self.assertIn("could not be read", detail.lower())

    def test_stale_email_outside_the_window_does_not_count(self):
        """A scanner that mailed three days ago is silent today.

        Seeded defect: take a healthy mailbox and age one scanner's mail past
        its window. The check must notice.
        """
        aged = [(ago(200) if "PEAD" in s else w, s) for w, s in QUIET_DAY]
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=aged)
        self.assertEqual(status, gmc_health.RED, detail)
        self.assertIn("pead", detail.lower())

    def test_a_quiet_conditional_scanner_does_not_fail_the_run(self):
        """13F mails only inside its filing window; its silence is the design.

        Grading it MISSING would manufacture a daily failure for a healthy
        scanner — the pa_watch permanent-yellow defect.
        """
        status, detail = gmc_health.check_scanner_emails(now=NOW, subjects=QUIET_DAY)
        self.assertEqual(status, gmc_health.GREEN)
        self.assertNotIn("13f", detail.lower().replace("not due", ""))

    # ---- A13 ------------------------------------------------------------- #

    def test_no_credential_value_can_reach_the_returned_detail(self):
        """The detail string is printed into an email and a log.

        The check must not interpolate an exception's text, because an IMAP
        failure's message is not under our control. Only the exception TYPE is
        allowed out — names and locations, never values (A13).
        """
        class Boom(Exception):
            pass

        with mock.patch.object(gmc_health, "_fetch_scanner_subjects",
                               side_effect=Boom("password=hunter2 leaked")):
            status, detail = gmc_health.check_scanner_emails(now=NOW)
        self.assertEqual(status, gmc_health.RED)
        self.assertNotIn("hunter2", detail)
        self.assertIn("Boom", detail)

    def test_fetch_passes_credentials_only_to_login_never_to_argv(self):
        """Structural: no subprocess call anywhere in the fetch path."""
        import inspect
        src = inspect.getsource(gmc_health._fetch_scanner_subjects)
        for banned in ("subprocess", "os.system", "argv", "print("):
            self.assertNotIn(banned, src,
                             f"{banned!r} appears in the credential-handling path")


class VendoredCopySyncTests(unittest.TestCase):
    """The vendored copy must not silently diverge from gmc_engine's original.

    A stale copy is the failure mode this design has to defend against: it would
    keep passing while the estate's real registry moved on.
    """

    def setUp(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "vendored", "PROVENANCE.json")) as fh:
            self.prov = json.load(fh)
        self.vendored = os.path.join(here, "vendored", "scanner_mail.py")

    def test_vendored_copy_matches_its_recorded_hash(self):
        """Catches local tampering with the copy."""
        with open(self.vendored, "rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(got, self.prov["sha256"],
                         "vendored/scanner_mail.py no longer matches PROVENANCE.json")

    def test_vendored_copy_matches_upstream_or_says_it_cannot_check(self):
        """Catches UPSTREAM drift when the engine checkout is on this host.

        A6: when the upstream file is absent this reports a NAMED
        cannot-verify state and fails the assertion-free path deliberately
        rather than passing quietly — a sync test that silently no-ops on the
        host it actually runs on is worse than none.
        """
        upstream = self.prov["upstream_abs_path_on_studio"]
        if not os.path.exists(upstream):
            self.skipTest(
                "CANNOT VERIFY UPSTREAM ON THIS HOST — %s is absent. This is "
                "expected on the MacBook watchdog (gmc_engine lives on the "
                "Studio) and is the reason the copy is vendored. Re-run this "
                "test on the Studio to check for drift." % upstream)
        with open(upstream, "rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(
            got, self.prov["sha256"],
            "UPSTREAM ops/obs/scanner_mail.py HAS CHANGED since vendoring at "
            "commit %s. Re-vendor and re-pin PROVENANCE.json." %
            self.prov["upstream_commit"])

    def test_registry_covers_both_subject_forms(self):
        """The vendored registry must recognise each scanner quiet AND loud.

        This is the old check's defect expressed as a property: a pattern set
        that only matches the quiet form loses the scanner the moment it has
        something to say.
        """
        for name, spec in scanner_mail.REGISTRY.items():
            for ex in spec.examples:
                self.assertTrue(spec.matches(ex),
                                f"{name} does not recognise its own example {ex!r}")


if __name__ == "__main__":
    unittest.main()
