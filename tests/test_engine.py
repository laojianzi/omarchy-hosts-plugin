from __future__ import annotations

import unittest

from omarchy_hosts.engine import (
    BEGIN_MARKER,
    END_MARKER,
    HostsError,
    build_plan,
    entries_to_text,
    normalize_address,
    normalize_hostname,
    parse_entries_text,
    profiles_config_sha256,
    split_managed_block,
)


def profile(
    profile_id: str,
    address: str = "127.0.0.1",
    names: tuple[str, ...] = ("app.test",),
    *,
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "id": profile_id,
        "name": profile_id.title(),
        "description": "",
        "enabled": enabled,
        "entries": [{"address": address, "names": list(names)}],
    }


class NormalizationTests(unittest.TestCase):
    def test_ipv4_and_ipv6_are_canonical(self) -> None:
        self.assertEqual(normalize_address(" 127.0.0.1 "), "127.0.0.1")
        self.assertEqual(normalize_address("2001:0db8::0001"), "2001:db8::1")

    def test_scoped_ipv6_is_rejected(self) -> None:
        with self.assertRaisesRegex(HostsError, "Scoped IPv6"):
            normalize_address("fe80::1%eth0")

    def test_idn_is_encoded_and_ascii_is_lowercased(self) -> None:
        self.assertEqual(normalize_hostname("BÜCHER.test"), "xn--bcher-kva.test")
        self.assertEqual(normalize_hostname("API_APP.TEST"), "api_app.test")

    def test_reserved_hostname_is_rejected(self) -> None:
        with self.assertRaises(HostsError) as raised:
            normalize_hostname("localhost")
        self.assertEqual(raised.exception.code, "reserved_hostname")

    def test_invalid_hostname_shapes_are_rejected(self) -> None:
        for value in ("*.test", "-bad.test", "bad-.test", "bad..test", "bad.test."):
            with self.subTest(value=value), self.assertRaises(HostsError):
                normalize_hostname(value)

    def test_entry_text_parses_comments_aliases_and_duplicates(self) -> None:
        entries = parse_entries_text(
            "# comment\n127.0.0.1 app.test api.test app.test # inline\n::1 v6.test\n"
        )
        self.assertEqual(
            entries,
            [
                {"address": "127.0.0.1", "names": ["app.test", "api.test"]},
                {"address": "::1", "names": ["v6.test"]},
            ],
        )
        self.assertEqual(entries_to_text(entries), "127.0.0.1 app.test api.test\n::1 v6.test\n")


class PlanningTests(unittest.TestCase):
    def test_first_insert_preserves_current_bytes(self) -> None:
        current = b"127.0.0.1 localhost\n# keep me\n"
        plan = build_plan(current, [profile("development")])
        self.assertTrue(plan.changed)
        self.assertTrue(plan.desired.startswith(current + b"\n"))
        self.assertIn(BEGIN_MARKER.encode(), plan.desired)
        self.assertIn(b"127.0.0.1 app.test", plan.desired)

    def test_existing_block_is_replaced_without_touching_surrounding_bytes(self) -> None:
        before = b"# before\n"
        old = f"{BEGIN_MARKER}\n127.0.0.1 old.test\n{END_MARKER}\n".encode()
        after = b"# after without final newline"
        plan = build_plan(before + old + after, [profile("new")])
        parts = split_managed_block(plan.desired)
        self.assertEqual(parts.before, before)
        self.assertEqual(parts.after, after)
        self.assertNotIn(b"old.test", parts.block)

    def test_crlf_style_is_preserved(self) -> None:
        current = b"127.0.0.1 localhost\r\n"
        plan = build_plan(current, [profile("crlf")])
        self.assertNotIn(b"\n", plan.desired.replace(b"\r\n", b""))

    def test_malformed_markers_fail_closed(self) -> None:
        with self.assertRaises(HostsError) as raised:
            build_plan((BEGIN_MARKER + "\n").encode(), [])
        self.assertEqual(raised.exception.code, "malformed_managed_block")

    def test_enabled_profile_conflict_is_blocking(self) -> None:
        profiles = [
            profile("one", "127.0.0.1", ("shared.test",)),
            profile("two", "10.0.0.2", ("shared.test",)),
        ]
        with self.assertRaises(HostsError) as raised:
            build_plan(b"", profiles)
        self.assertEqual(raised.exception.code, "profile_conflict")

    def test_unmanaged_conflict_is_blocking(self) -> None:
        with self.assertRaises(HostsError) as raised:
            build_plan(b"10.0.0.2 app.test\n", [profile("one")])
        self.assertEqual(raised.exception.code, "unmanaged_conflict")

    def test_same_unmanaged_mapping_warns_and_is_not_duplicated(self) -> None:
        plan = build_plan(b"127.0.0.1 app.test\n", [profile("one")])
        self.assertFalse(plan.changed)
        self.assertEqual(plan.warnings[0]["code"], "provided_by_unmanaged_line")

    def test_same_profile_mapping_is_deduplicated(self) -> None:
        plan = build_plan(
            b"",
            [profile("one"), profile("two", names=("app.test", "api.test"))],
        )
        self.assertEqual(plan.desired.count(b"app.test"), 1)
        self.assertEqual(plan.warnings[0]["code"], "duplicate_profile_mapping")

    def test_disabled_profiles_do_not_affect_configuration_hash(self) -> None:
        base = [profile("one")]
        with_disabled = base + [profile("two", "10.0.0.2", ("ignored.test",), enabled=False)]
        self.assertEqual(profiles_config_sha256(base), profiles_config_sha256(with_disabled))

    def test_rendering_is_deterministic(self) -> None:
        current = b"127.0.0.1 localhost\n"
        first = build_plan(current, [profile("one")])
        second = build_plan(current, [profile("one")])
        self.assertEqual(first.desired, second.desired)
        self.assertEqual(first.desired_sha256, second.desired_sha256)
        self.assertIn("/etc/hosts (proposed)", first.diff)


if __name__ == "__main__":
    unittest.main()
