#!/usr/bin/env python3
"""Tests for gmc_health.check_ib_gateway — the Studio Gateway network probe.

Stdlib unittest (no pytest dependency). Run: python3 -m unittest -v test_gmc_health

check_ib_gateway is a NETWORK REACHABILITY PROBE to the Studio's IB Gateway
paper port (4002), not a local process match. Behaviour under test:

  * TCP connect succeeds            -> GREEN (any time of day)
  * connect fails, before 07:35 CT  -> YELLOW "not started yet — expected"
                                       (Studio Gateway starts ~07:30; mirrors the
                                        morning-brief pre-window pattern)
  * connect fails, at/after 07:35   -> RED
"""

import datetime
import unittest
from unittest import mock

import gmc_health


def _at(hh, mm):
    """A naive datetime at the given local (CT) hour/minute, date fixed."""
    return datetime.datetime(2026, 7, 20, hh, mm, 0)


class CheckIbGatewayTests(unittest.TestCase):

    def test_reachable_is_green_after_window(self):
        with mock.patch.object(gmc_health, "_tcp_reachable", return_value=True), \
             mock.patch.object(gmc_health, "_studio_host", return_value="studio.local"):
            status, detail = gmc_health.check_ib_gateway(now=_at(6, 0))
        self.assertEqual(status, gmc_health.GREEN)

    def test_reachable_is_green_even_before_window(self):
        # Gateway came up early — reachability wins over the time gate.
        with mock.patch.object(gmc_health, "_tcp_reachable", return_value=True), \
             mock.patch.object(gmc_health, "_studio_host", return_value="studio.local"):
            status, _ = gmc_health.check_ib_gateway(now=_at(6, 0))
        self.assertEqual(status, gmc_health.GREEN)

    def test_unreachable_before_window_is_yellow_expected(self):
        # 06:00 email, Studio Gateway not up until ~07:30 -> not an alarm.
        with mock.patch.object(gmc_health, "_tcp_reachable", return_value=False), \
             mock.patch.object(gmc_health, "_studio_host", return_value="studio.local"):
            status, detail = gmc_health.check_ib_gateway(now=_at(6, 0))
        self.assertEqual(status, gmc_health.YELLOW)
        self.assertIn("expected", detail.lower())

    def test_unreachable_just_before_cutoff_is_yellow(self):
        with mock.patch.object(gmc_health, "_tcp_reachable", return_value=False), \
             mock.patch.object(gmc_health, "_studio_host", return_value="studio.local"):
            status, _ = gmc_health.check_ib_gateway(now=_at(7, 34))
        self.assertEqual(status, gmc_health.YELLOW)

    def test_unreachable_at_cutoff_is_red(self):
        with mock.patch.object(gmc_health, "_tcp_reachable", return_value=False), \
             mock.patch.object(gmc_health, "_studio_host", return_value="studio.local"):
            status, _ = gmc_health.check_ib_gateway(now=_at(7, 35))
        self.assertEqual(status, gmc_health.RED)

    def test_unreachable_after_window_is_red(self):
        with mock.patch.object(gmc_health, "_tcp_reachable", return_value=False), \
             mock.patch.object(gmc_health, "_studio_host", return_value="studio.local"):
            status, detail = gmc_health.check_ib_gateway(now=_at(8, 0))
        self.assertEqual(status, gmc_health.RED)

    def test_probes_the_paper_port_4002(self):
        seen = {}

        def fake_probe(host, port, timeout=None):
            seen["host"], seen["port"] = host, port
            return True

        with mock.patch.object(gmc_health, "_tcp_reachable", side_effect=fake_probe), \
             mock.patch.object(gmc_health, "_studio_host", return_value="studio.local"):
            gmc_health.check_ib_gateway(now=_at(6, 0))
        self.assertEqual(seen["port"], 4002)
        self.assertEqual(seen["host"], "studio.local")


if __name__ == "__main__":
    unittest.main()
