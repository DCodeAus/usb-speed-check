#!/usr/bin/env python3
"""
usb_speed.py — check how fast a USB drive (and its cable) really is, on macOS.

Why this exists
---------------
You can't measure a cable on its own. A USB link's speed is whatever the
drive, the cable and the port NEGOTIATE together. So this tool reports two
things and compares them:

  1. The negotiated link speed  (e.g. "USB 3.1 Gen 2 (10 Gb/s)") — read from
     macOS. If a fast drive negotiates a slow link, the cable or the port is
     the weak link. That's the usual way a bad/old cable shows up.

  2. The real throughput  — it times how fast it can READ a large file that is
     already on the drive, to show the MB/s you actually get.

Then it tells you, in plain English, whether the cable is holding you back.

Why read, not write? On macOS the OS caches writes in RAM, so a write test
reports the speed of RAM (many GB/s), not the drive — especially on exFAT,
where the only way to force a write to disk (fsync) is painfully slow. Timing a
read of a real, already-stored file sidesteps all that, adds no drive wear, and
is accurate as long as the file wasn't just accessed. The tool also sanity-
checks the number and discards it if it looks like it came from the cache.

How to run it
-------------
    python3 usb_speed.py                 # pick from your plugged-in USB drives
    python3 usb_speed.py "T7 Shield"     # test a drive by name
    python3 usb_speed.py /Volumes/T7\\ Shield
    python3 usb_speed.py --size 2        # read up to 2 GiB when timing (default 1)
    python3 usb_speed.py --no-throughput # just report the negotiated link speed

No extra packages needed — just the Python 3 that ships with macOS. The tool
only reads from the drive; it writes nothing.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import plistlib
import re
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# macOS reports a USB link's speed as an enum in `system_profiler`. We map each
# to a friendly name and to the practical top throughput (MB/s) you can really
# reach on that link after overhead, so we can judge whether a measured number
# is "about right" or suspiciously low.
# ---------------------------------------------------------------------------
SPEED_TIERS = {
    "low_speed":            ("USB 1.0 (1.5 Mb/s)", 0.2),
    "full_speed":           ("USB 1.1 (12 Mb/s)", 1),
    "high_speed":           ("USB 2.0 (480 Mb/s)", 45),
    "super_speed":          ("USB 3.0 / 3.1 Gen 1 (5 Gb/s)", 450),
    "super_speed_plus":     ("USB 3.1 Gen 2 (10 Gb/s)", 1000),
    "super_speed_plus_20":  ("USB 3.2 Gen 2x2 (20 Gb/s)", 2000),
    "super_speed_plus_2x2": ("USB 3.2 Gen 2x2 (20 Gb/s)", 2000),
}

# macOS-only flag (from <fcntl.h>): tell the kernel NOT to cache a file's data,
# so the read test measures the real drive instead of RAM.
F_NOCACHE = 48


def run(cmd: list[str]) -> str:
    """Run a command and return its stdout as text."""
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def run_plist(cmd: list[str]) -> dict:
    """Run a command that prints a plist and parse it into a dict."""
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return plistlib.loads(out)


def whole_disk(device_node: str) -> str:
    """Turn a volume node like '/dev/disk4s1' into its whole disk 'disk4'."""
    m = re.search(r"(disk\d+)", device_node)
    return m.group(1) if m else device_node.replace("/dev/", "")


def list_usb_volumes() -> list[dict]:
    """Return mounted external USB disks as {name, mount, disk, bus}."""
    vols = []
    for name in sorted(os.listdir("/Volumes")):
        mount = os.path.join("/Volumes", name)
        if not os.path.ismount(mount):
            continue
        try:
            info = run_plist(["diskutil", "info", "-plist", mount])
        except subprocess.CalledProcessError:
            continue
        # Only external drives; skip the internal Macintosh HD etc.
        if info.get("Internal", True):
            continue
        node = info.get("DeviceNode", "")
        if not node:
            continue
        vols.append({
            "name": info.get("VolumeName") or name,
            "mount": mount,
            "disk": whole_disk(node),
            "bus": info.get("BusProtocol", "USB"),
        })
    return vols


def negotiated_speed(disk: str):
    """Find the negotiated link speed for a disk (e.g. 'disk4'). Returns
    (friendly_label, practical_MBps) or None if it can't be read."""
    data = json.loads(run(["system_profiler", "-json", "SPUSBDataType"]))

    def walk(items):
        for item in items:
            for m in item.get("Media", []):
                if m.get("bsd_name") == disk:
                    return item.get("device_speed")
            found = walk(item.get("_items", []))
            if found:
                return found
        return None

    raw = walk(data.get("SPUSBDataType", []))
    if not raw:
        return None
    return SPEED_TIERS.get(raw, (raw.replace("_", " "), 0))


MIN_TEST_FILE = 256 * 1024 ** 2  # smallest existing file worth timing (256 MiB)


def find_large_file(mount: str, min_bytes: int = MIN_TEST_FILE) -> str | None:
    """Find an existing file on the drive big enough to time a read against.

    We measure throughput by READING a file that is already on the drive —
    never by writing. On macOS + exFAT, writes just land in RAM cache (so a
    write test reports fantasy speeds) and the only way to force them to disk,
    fsync, crawls. Reading a real, already-stored file avoids all of that, adds
    no drive wear, and is cache-free as long as the file wasn't just accessed.
    """
    for root, dirs, files in os.walk(mount):
        dirs[:] = [d for d in dirs if not d.startswith(".")]  # skip hidden dirs
        for name in files:
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) >= min_bytes:
                    return path
            except OSError:
                continue
    return None


def read_speed(path: str, cap_gib: float) -> float:
    """Read up to cap_gib from a file, bypassing the cache, and return MB/s."""
    chunk = 8 * 1024 * 1024  # 8 MiB blocks
    cap = int(cap_gib * 1024 ** 3)
    fd = os.open(path, os.O_RDONLY)
    try:
        # Ask macOS to bypass its cache so we measure the drive, not RAM.
        try:
            ctypes.CDLL("libc.dylib", use_errno=True).fcntl(fd, F_NOCACHE, 1)
        except Exception:
            pass
        start = time.monotonic()
        read = 0
        while read < cap:
            data = os.read(fd, chunk)
            if not data:
                break
            read += len(data)
    finally:
        os.close(fd)
    return read / (time.monotonic() - start) / 1e6


def pick_volume(volumes: list[dict], query: str | None) -> dict:
    """Choose which volume to test, from an argument or an interactive prompt."""
    if query:
        q = query.rstrip("/")
        for v in volumes:
            if q in (v["name"], v["mount"], v["mount"].rstrip("/")):
                return v
        found = ", ".join(v["name"] for v in volumes) or "(none)"
        sys.exit(f"No plugged-in USB drive matches '{query}'. Found: {found}")
    if not volumes:
        sys.exit("No external USB drives are mounted. Plug one in and try again.")
    if len(volumes) == 1:
        return volumes[0]
    print("Which drive do you want to test?")
    for i, v in enumerate(volumes, 1):
        print(f"  {i}. {v['name']}  ({v['bus']}, {v['mount']})")
    try:
        return volumes[int(input("Number: ").strip()) - 1]
    except (ValueError, IndexError):
        sys.exit("Not a valid choice.")


def looks_cached(speed: float, practical: float) -> bool:
    """A read far above the link's ceiling can only have come from RAM cache."""
    if practical:
        return speed > practical * 1.4
    return speed > 1500  # no link info: assume cached if it beats any real USB link


def link_verdict(label: str, practical: float) -> str:
    """Verdict based on the negotiated link speed alone (always reliable)."""
    if not practical:
        return "ℹ️  Couldn't read the negotiated link speed on this Mac."
    if practical <= 45:  # USB 2.0 or slower
        return ("⚠️  This link negotiated USB 2.0 speed (or slower). A fast drive "
                "stuck here is almost always a slow/old cable or a USB 2.0 port.\n"
                "    Try a USB 3+ (or Thunderbolt) cable and a USB 3+ port.")
    return (f"✅  The cable and port negotiated {label} — a fast link, so the "
            "cable is not limiting you here.")


def throughput_note(speed: float, label: str, practical: float) -> str:
    """One line interpreting the measured read speed (or why it's unavailable)."""
    if not speed:
        return ("ℹ️  No file ≥256 MiB on the drive to time a read against, so "
                "throughput wasn't measured. The link speed above is the reliable "
                "figure; copy a large file over if you want a throughput number.")
    if looks_cached(speed, practical):
        return (f"ℹ️  The read came back at {speed:,.0f} MB/s — that's the RAM "
                "cache, not the drive, so it's not a real throughput figure. The "
                "link speed above is the reliable one.")
    if practical:
        pct = speed / practical * 100
        return (f"    Real read throughput: {speed:,.0f} MB/s (~{pct:.0f}% of this "
                "link's practical ceiling — normal for a drive like this).")
    return f"    Real read throughput: {speed:,.0f} MB/s."


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("This tool uses macOS commands (system_profiler, diskutil) and "
                 "only runs on a Mac.")

    ap = argparse.ArgumentParser(
        description="Check a USB drive's real speed and whether its cable is "
                    "holding it back.")
    ap.add_argument("volume", nargs="?",
                    help="Drive name or /Volumes path (default: ask / auto-pick)")
    ap.add_argument("--size", type=float, default=1.0,
                    help="How much to read (GiB) when timing throughput (default 1)")
    ap.add_argument("--no-throughput", action="store_true",
                    help="Only report the negotiated link speed; skip the read test")
    args = ap.parse_args()

    vol = pick_volume(list_usb_volumes(), args.volume)

    print(f"\nDrive:  {vol['name']}  ({vol['mount']})")
    spd = negotiated_speed(vol["disk"])
    label, practical = spd if spd else ("unknown", 0)
    print(f"Link:   {label if spd else 'could not read the negotiated speed'}")
    print()
    print(link_verdict(label, practical))

    if args.no_throughput:
        print()
        return

    big = find_large_file(vol["mount"])
    speed = read_speed(big, args.size) if big else 0.0
    print()
    print(throughput_note(speed, label, practical))
    print()


if __name__ == "__main__":
    main()
