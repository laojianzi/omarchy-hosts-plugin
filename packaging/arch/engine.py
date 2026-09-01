"""Pure validation, conflict detection, rendering, and diff planning.

The unprivileged panel backend and the root-owned writer install the same copy
of this module. Keeping the policy engine side-effect free makes preview and
apply deterministic and independently revalidated at the privilege boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import ipaddress
import json
import re
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
BEGIN_MARKER = "# >>> omarchy-hosts managed block >>>"
END_MARKER = "# <<< omarchy-hosts managed block <<<"
MAX_HOSTS_BYTES = 2 * 1024 * 1024
MAX_PROFILES = 256
MAX_ENTRIES = 10_000
MAX_NAMES_PER_ENTRY = 64
MAX_DIFF_CHARS = 200_000

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ASCII_HOST_RE = re.compile(r"^[a-z0-9_-]+$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# These names are conventionally owned by the operating system. Loopback
# addresses remain valid for development names such as app.test.
RESERVED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "localhost4",
        "localhost4.localdomain4",
        "localhost6",
        "localhost6.localdomain6",
        "ip6-localhost",
        "ip6-loopback",
        "ip6-allnodes",
        "ip6-allrouters",
        "broadcasthost",
    }
)


class HostsError(ValueError):
    """Stable user-displayable error with a machine-readable code."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class BlockParts:
    before: bytes
    block: bytes
    after: bytes
    present: bool

    @property
    def unmanaged(self) -> bytes:
        return self.before + self.after


@dataclass(frozen=True)
class Plan:
    current: bytes
    desired: bytes
    current_sha256: str
    desired_sha256: str
    managed_sha256: str
    actual_managed_sha256: str
    managed_present: bool
    enabled_profile_count: int
    rendered_entry_count: int
    rendered_name_count: int
    warnings: tuple[dict[str, Any], ...]
    diff: str

    @property
    def changed(self) -> bool:
        return self.current != self.desired

    def to_dict(self, *, include_diff: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "currentSha256": self.current_sha256,
            "desiredSha256": self.desired_sha256,
            "managedSha256": self.managed_sha256,
            "actualManagedSha256": self.actual_managed_sha256,
            "managedPresent": self.managed_present,
            "enabledProfileCount": self.enabled_profile_count,
            "renderedEntryCount": self.rendered_entry_count,
            "renderedNameCount": self.rendered_name_count,
            "warnings": list(self.warnings),
            "changed": self.changed,
        }
        if include_diff:
            data["diff"] = self.diff
        return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX_SHA256_RE.fullmatch(value))


def _clean_string(value: Any, *, field: str, maximum: int, required: bool = False) -> str:
    text = "" if value is None else str(value)
    if "\x00" in text or "\r" in text or "\n" in text:
        raise HostsError("invalid_text", f"{field} cannot contain NUL or line breaks", {"field": field})
    text = text.strip()
    if required and not text:
        raise HostsError("required", f"{field} is required", {"field": field})
    if len(text) > maximum:
        raise HostsError(
            "too_long",
            f"{field} must be at most {maximum} characters",
            {"field": field, "maximum": maximum},
        )
    return text


def normalize_profile_id(value: Any) -> str:
    profile_id = _clean_string(value, field="Profile id", maximum=64, required=True).lower()
    if not PROFILE_ID_RE.fullmatch(profile_id):
        raise HostsError(
            "invalid_profile_id",
            "Profile id must start with a letter or digit and contain only lowercase letters, digits, '.', '_' or '-'",
            {"profileId": profile_id},
        )
    return profile_id


def normalize_address(value: Any) -> str:
    text = _clean_string(value, field="IP address", maximum=64, required=True)
    if "%" in text:
        raise HostsError(
            "invalid_address",
            f"Scoped IPv6 addresses are not supported in /etc/hosts: {text}",
            {"address": text},
        )
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError as exc:
        raise HostsError("invalid_address", f"Invalid IP address: {text}", {"address": text}) from exc


def normalize_hostname(value: Any) -> str:
    raw = _clean_string(value, field="Hostname", maximum=253, required=True)
    if raw.endswith("."):
        raise HostsError("invalid_hostname", f"Hostname must not end with a dot: {raw}", {"hostname": raw})

    labels: list[str] = []
    for original_label in raw.split("."):
        if not original_label:
            raise HostsError("invalid_hostname", f"Hostname has an empty label: {raw}", {"hostname": raw})
        try:
            if original_label.isascii():
                label = original_label.lower()
            else:
                label = original_label.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise HostsError("invalid_hostname", f"Hostname cannot be IDNA encoded: {raw}", {"hostname": raw}) from exc
        if len(label) > 63 or not ASCII_HOST_RE.fullmatch(label):
            raise HostsError(
                "invalid_hostname",
                f"Invalid hostname label '{original_label}' in {raw}",
                {"hostname": raw, "label": original_label},
            )
        if label.startswith("-") or label.endswith("-"):
            raise HostsError(
                "invalid_hostname",
                f"Hostname labels cannot start or end with '-': {raw}",
                {"hostname": raw},
            )
        labels.append(label)

    hostname = ".".join(labels)
    if len(hostname) > 253:
        raise HostsError("invalid_hostname", f"Hostname is too long: {raw}", {"hostname": raw})
    if hostname in RESERVED_HOSTNAMES:
        raise HostsError(
            "reserved_hostname",
            f"'{hostname}' is reserved by the operating system and cannot be managed",
            {"hostname": hostname},
        )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise HostsError(
            "invalid_hostname",
            f"An IP literal cannot be used as a hostname: {hostname}",
            {"hostname": hostname},
        )
    return hostname


def normalize_entry(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise HostsError("invalid_entry", "Each hosts entry must be an object")
    address = normalize_address(raw.get("address"))
    names_raw = raw.get("names")
    if isinstance(names_raw, str):
        names_source: Sequence[Any] = names_raw.split()
    elif isinstance(names_raw, Sequence) and not isinstance(names_raw, (bytes, bytearray)):
        names_source = names_raw
    else:
        raise HostsError("invalid_entry", "Each hosts entry needs a list of hostnames", {"address": address})
    if not names_source:
        raise HostsError("invalid_entry", "Each hosts entry needs at least one hostname", {"address": address})
    if len(names_source) > MAX_NAMES_PER_ENTRY:
        raise HostsError(
            "too_many_names",
            f"An entry may contain at most {MAX_NAMES_PER_ENTRY} hostnames",
            {"address": address, "maximum": MAX_NAMES_PER_ENTRY},
        )
    names: list[str] = []
    seen: set[str] = set()
    for value in names_source:
        name = normalize_hostname(value)
        if name not in seen:
            names.append(name)
            seen.add(name)
    return {"address": address, "names": names}


def normalize_profile(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise HostsError("invalid_profile", "Each profile must be an object")
    profile_id = normalize_profile_id(raw.get("id"))
    name = _clean_string(raw.get("name"), field="Profile name", maximum=80, required=True)
    description = _clean_string(raw.get("description"), field="Description", maximum=500)
    enabled = raw.get("enabled") is True
    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, Sequence) or isinstance(entries_raw, (str, bytes, bytearray)):
        raise HostsError("invalid_profile", "Profile entries must be a list", {"profileId": profile_id})
    entries = [normalize_entry(entry) for entry in entries_raw]
    return {
        "id": profile_id,
        "name": name,
        "description": description,
        "enabled": enabled,
        "entries": entries,
    }


def normalize_profiles(raw_profiles: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_profiles, Sequence) or isinstance(raw_profiles, (str, bytes, bytearray)):
        raise HostsError("invalid_profiles", "Profiles must be a list")
    if len(raw_profiles) > MAX_PROFILES:
        raise HostsError("too_many_profiles", f"At most {MAX_PROFILES} profiles are supported")
    profiles: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    total_entries = 0
    for raw in raw_profiles:
        profile = normalize_profile(raw)
        if profile["id"] in seen_ids:
            raise HostsError(
                "duplicate_profile_id",
                f"Duplicate profile id: {profile['id']}",
                {"profileId": profile["id"]},
            )
        seen_ids.add(profile["id"])
        total_entries += len(profile["entries"])
        if total_entries > MAX_ENTRIES:
            raise HostsError("too_many_entries", f"At most {MAX_ENTRIES} total entries are supported")
        profiles.append(profile)
    return profiles


def parse_entries_text(text: Any) -> list[dict[str, Any]]:
    source = "" if text is None else str(text)
    if "\x00" in source:
        raise HostsError("invalid_text", "Profile entries cannot contain NUL bytes")
    if len(source.encode("utf-8")) > MAX_HOSTS_BYTES:
        raise HostsError("entries_too_large", "Profile entries are too large")
    entries: list[dict[str, Any]] = []
    for line_number, source_line in enumerate(source.splitlines(), start=1):
        content = source_line.split("#", 1)[0].strip()
        if not content:
            continue
        fields = content.split()
        if len(fields) < 2:
            raise HostsError(
                "invalid_entry_line",
                f"Line {line_number}: expected 'IP hostname [alias ...]'",
                {"line": line_number, "source": source_line},
            )
        try:
            address = normalize_address(fields[0])
            names = [normalize_hostname(name) for name in fields[1:]]
        except HostsError as exc:
            details = dict(exc.details)
            details["line"] = line_number
            details["source"] = source_line
            raise HostsError(exc.code, f"Line {line_number}: {exc.message}", details) from exc
        if len(names) > MAX_NAMES_PER_ENTRY:
            raise HostsError(
                "too_many_names",
                f"Line {line_number}: at most {MAX_NAMES_PER_ENTRY} hostnames are allowed",
                {"line": line_number},
            )
        entries.append({"address": address, "names": list(dict.fromkeys(names))})
        if len(entries) > MAX_ENTRIES:
            raise HostsError("too_many_entries", f"At most {MAX_ENTRIES} entries are supported")
    return entries


def entries_to_text(entries: Iterable[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for raw in entries:
        entry = normalize_entry(raw)
        lines.append(entry["address"] + " " + " ".join(entry["names"]))
    return "\n".join(lines) + ("\n" if lines else "")


def profiles_config_sha256(profiles: Any) -> str:
    normalized = normalize_profiles(profiles)
    enabled = [
        {
            "id": profile["id"],
            "name": profile["name"],
            "entries": [
                {"address": entry["address"], "names": entry["names"]}
                for entry in profile["entries"]
            ],
        }
        for profile in normalized
        if profile["enabled"]
    ]
    encoded = json.dumps(enabled, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def split_managed_block(content: bytes) -> BlockParts:
    if len(content) > MAX_HOSTS_BYTES:
        raise HostsError(
            "hosts_too_large",
            f"/etc/hosts exceeds the {MAX_HOSTS_BYTES // 1024} KiB safety limit",
            {"size": len(content), "maximum": MAX_HOSTS_BYTES},
        )
    lines = content.splitlines(keepends=True)
    begin = BEGIN_MARKER.encode("utf-8")
    end = END_MARKER.encode("utf-8")
    begin_indexes = [index for index, line in enumerate(lines) if line.rstrip(b"\r\n") == begin]
    end_indexes = [index for index, line in enumerate(lines) if line.rstrip(b"\r\n") == end]
    if not begin_indexes and not end_indexes:
        return BlockParts(content, b"", b"", False)
    if len(begin_indexes) != 1 or len(end_indexes) != 1 or begin_indexes[0] >= end_indexes[0]:
        raise HostsError(
            "malformed_managed_block",
            "The Omarchy Hosts markers in /etc/hosts are incomplete, duplicated, or out of order",
            {"beginMarkers": len(begin_indexes), "endMarkers": len(end_indexes)},
        )
    start = begin_indexes[0]
    finish = end_indexes[0]
    return BlockParts(
        before=b"".join(lines[:start]),
        block=b"".join(lines[start : finish + 1]),
        after=b"".join(lines[finish + 1 :]),
        present=True,
    )


def _preferred_newline(content: bytes) -> bytes:
    crlf = content.count(b"\r\n")
    all_lf = content.count(b"\n")
    return b"\r\n" if crlf and crlf == all_lf else b"\n"


def _parse_unmanaged_mappings(content: bytes) -> dict[str, set[str]]:
    mappings: dict[str, set[str]] = {}
    text = content.decode("utf-8", errors="replace")
    for line in text.splitlines():
        payload = line.split("#", 1)[0].strip()
        if not payload:
            continue
        fields = payload.split()
        if len(fields) < 2:
            continue
        try:
            address = normalize_address(fields[0])
        except HostsError:
            continue
        for raw_name in fields[1:]:
            try:
                name = normalize_hostname(raw_name)
            except HostsError:
                # Reserved and non-standard system names cannot be desired by a
                # valid profile, but keeping a normalized lookup key is useful.
                name = raw_name.rstrip(".").lower()
            mappings.setdefault(name, set()).add(address)
    return mappings


def _dedupe_enabled_profiles(
    profiles: Sequence[Mapping[str, Any]], unmanaged: bytes
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unmanaged_map = _parse_unmanaged_mappings(unmanaged)
    claimed: dict[str, tuple[str, str, str]] = {}
    warnings: list[dict[str, Any]] = []
    rendered_profiles: list[dict[str, Any]] = []

    for profile in profiles:
        if not profile["enabled"]:
            continue
        rendered_entries: list[dict[str, Any]] = []
        for entry in profile["entries"]:
            address = entry["address"]
            render_names: list[str] = []
            for name in entry["names"]:
                previous = claimed.get(name)
                if previous is not None:
                    old_address, old_profile_id, old_profile_name = previous
                    if old_address != address:
                        raise HostsError(
                            "profile_conflict",
                            f"'{name}' maps to both {old_address} and {address} in enabled profiles",
                            {
                                "hostname": name,
                                "firstAddress": old_address,
                                "secondAddress": address,
                                "firstProfileId": old_profile_id,
                                "firstProfileName": old_profile_name,
                                "secondProfileId": profile["id"],
                                "secondProfileName": profile["name"],
                            },
                        )
                    warnings.append(
                        {
                            "code": "duplicate_profile_mapping",
                            "message": f"'{name}' is repeated with the same address; only the first mapping is rendered",
                            "hostname": name,
                            "address": address,
                            "profileId": profile["id"],
                        }
                    )
                    continue

                existing = unmanaged_map.get(name, set())
                different = sorted(ip for ip in existing if ip != address)
                if different:
                    raise HostsError(
                        "unmanaged_conflict",
                        f"'{name}' already maps to {', '.join(different)} outside the managed block",
                        {
                            "hostname": name,
                            "desiredAddress": address,
                            "unmanagedAddresses": sorted(existing),
                            "profileId": profile["id"],
                            "profileName": profile["name"],
                        },
                    )
                claimed[name] = (address, profile["id"], profile["name"])
                if address in existing:
                    warnings.append(
                        {
                            "code": "provided_by_unmanaged_line",
                            "message": f"'{name}' already has the same mapping outside the managed block and will not be duplicated",
                            "hostname": name,
                            "address": address,
                            "profileId": profile["id"],
                        }
                    )
                    continue
                render_names.append(name)
            if render_names:
                rendered_entries.append({"address": address, "names": render_names})
        if rendered_entries:
            rendered_profiles.append(
                {"id": profile["id"], "name": profile["name"], "entries": rendered_entries}
            )
    return rendered_profiles, warnings


def render_managed_block(profiles: Sequence[Mapping[str, Any]], newline: bytes = b"\n") -> bytes:
    if not profiles:
        return b""
    lines: list[str] = [
        BEGIN_MARKER,
        "# Managed by io.omarchy.hosts. Edit profiles through Omarchy Hosts, not this block.",
    ]
    for index, profile in enumerate(profiles):
        if index:
            lines.append("#")
        lines.append(f"# profile: {profile['name']} [{profile['id']}]")
        for entry in profile["entries"]:
            lines.append(f"{entry['address']} {' '.join(entry['names'])}")
    lines.append(END_MARKER)
    return newline.join(line.encode("utf-8") for line in lines) + newline


def replace_managed_block(parts: BlockParts, block: bytes, current: bytes) -> bytes:
    if parts.present:
        return parts.before + block + parts.after
    if not block:
        return current
    if not current:
        return block
    newline = _preferred_newline(current)
    if current.endswith(b"\r\n\r\n") or current.endswith(b"\n\n"):
        separator = b""
    elif current.endswith(b"\n"):
        separator = newline
    else:
        separator = newline + newline
    return current + separator + block


def unified_diff(current: bytes, desired: bytes) -> str:
    if current == desired:
        return ""
    current_lines = current.decode("utf-8", errors="replace").splitlines(keepends=True)
    desired_lines = desired.decode("utf-8", errors="replace").splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            current_lines,
            desired_lines,
            fromfile="/etc/hosts (current)",
            tofile="/etc/hosts (proposed)",
            n=3,
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n… diff truncated by safety limit …\n"
    return diff


def build_plan(current: bytes, raw_profiles: Any) -> Plan:
    if not isinstance(current, (bytes, bytearray)):
        raise TypeError("current must be bytes")
    current_bytes = bytes(current)
    profiles = normalize_profiles(raw_profiles)
    parts = split_managed_block(current_bytes)
    rendered_profiles, warnings = _dedupe_enabled_profiles(profiles, parts.unmanaged)
    newline = _preferred_newline(current_bytes)
    block = render_managed_block(rendered_profiles, newline)
    desired = replace_managed_block(parts, block, current_bytes)
    if len(block) > MAX_HOSTS_BYTES or len(desired) > MAX_HOSTS_BYTES:
        raise HostsError(
            "managed_block_too_large",
            f"The rendered /etc/hosts content exceeds the {MAX_HOSTS_BYTES // 1024} KiB safety limit",
            {
                "managedSize": len(block),
                "desiredSize": len(desired),
                "maximum": MAX_HOSTS_BYTES,
            },
        )
    entry_count = sum(len(profile["entries"]) for profile in rendered_profiles)
    name_count = sum(
        len(entry["names"])
        for profile in rendered_profiles
        for entry in profile["entries"]
    )
    return Plan(
        current=current_bytes,
        desired=desired,
        current_sha256=sha256_bytes(current_bytes),
        desired_sha256=sha256_bytes(desired),
        managed_sha256=sha256_bytes(block) if block else "",
        actual_managed_sha256=sha256_bytes(parts.block) if parts.present else "",
        managed_present=parts.present,
        enabled_profile_count=sum(1 for profile in profiles if profile["enabled"]),
        rendered_entry_count=entry_count,
        rendered_name_count=name_count,
        warnings=tuple(warnings),
        diff=unified_diff(current_bytes, desired),
    )
