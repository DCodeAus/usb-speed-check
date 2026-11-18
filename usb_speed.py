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

  2. The real throughput  — it writes a test file to the drive and reads it
     back, timing both, to show the MB/s you actually get.

Then it tells you, in plain English, whether the cable is holding you back.

How to run it
-------------
    python3 usb_speed.py                 # pick from your plugged-in USB drives
    python3 usb_speed.py "T7 Shield"     # test a drive by name
    python3 usb_speed.py /Volumes/T7\\ Shield
    python3 usb_speed.py --size 2        # use a 2 GiB test file (default 1)
    python3 usb_speed.py --read-only     # only time reading (needs the file to exist)

No extra packages needed — just the Python 3 that ships with macOS.
The test file is created and then deleted automatically.
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


def benchmark(mount: str, size_gib: float, do_write: bool, do_read: bool) -> dict:
    """Write a test file then read it back (uncached), timing each phase.
    Speeds are in MB/s (decimal millions of bytes, like drive specs use)."""
    path = os.path.join(mount, ".usb_speed_test.bin")
    nbytes = int(size_gib * 1024 ** 3)
    chunk = 8 * 1024 * 1024  # 8 MiB blocks
    buf = os.urandom(chunk)  # random data: fair to drives that compress zeros
    result = {}

    if do_write:
        start = time.monotonic()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            written = 0
            while written < nbytes:
                written += os.write(fd, buf)
            os.fsync(fd)  # force it to the drive, not just the OS buffer
        finally:
            os.close(fd)
        result["write"] = written / (time.monotonic() - start) / 1e6

    if do_read:
        if not os.path.exists(path):
            raise FileNotFoundError(
                "No test file to read. Run a write test first (drop --read-only)."
            )
        fd = os.open(path, os.O_RDONLY)
        try:
            # Ask macOS to bypass its cache so we measure the drive, not RAM.
            try:
                ctypes.CDLL("libc.dylib", use_errno=True).fcntl(fd, F_NOCACHE, 1)
            except Exception:
                pass  # worst case we get a cache-influenced number
            start = time.monotonic()
            read = 0
            while True:
                data = os.read(fd, chunk)
                if not data:
                    break
                read += len(data)
        finally:
            os.close(fd)
        result["read"] = read / (time.monotonic() - start) / 1e6

    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return result


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


def verdict(label: str, practical: float, best: float) -> str:
    """Plain-English judgement comparing measured speed to the link's ceiling."""
    if not best:
        return ""
    if not practical:
        return ("ℹ️  Measured the throughput, but couldn't read the link speed "
                "to compare against.")
    if practical <= 45:  # USB 2.0 or slower
        return ("⚠️  This link negotiated USB 2.0 speed (or slower). A fast drive "
                "stuck here is almost always a slow/old cable or a USB 2.0 port.\n"
                "    Try a USB 3+ (or Thunderbolt) cable and a USB 3+ port.")
    pct = best / practical * 100
    if pct >= 55:
        return (f"✅  You're getting about {pct:.0f}% of this link's practical "
                "ceiling — that's normal. The cable is NOT the bottleneck.")
    return (f"🟡  Measured speed is only ~{pct:.0f}% of what this {label} link "
            "can do.\n    That can be the drive itself, a busy drive, or a "
            "marginal cable. Re-run; if it stays low, try another cable/port.")


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
                    help="Test-file size in GiB (default 1)")
    ap.add_argument("--read-only", action="store_true", help="Only time reading")
    ap.add_argument("--write-only", action="store_true", help="Only time writing")
    args = ap.parse_args()

    do_write = not args.read_only
    do_read = not args.write_only

    vol = pick_volume(list_usb_volumes(), args.volume)

    print(f"\nDrive:  {vol['name']}  ({vol['mount']})")
    spd = negotiated_speed(vol["disk"])
    if spd:
        label, practical = spd
        print(f"Link:   {label}")
    else:
        label, practical = "unknown", 0
        print("Link:   could not read the negotiated speed")
    print(f"Test:   {args.size:g} GiB  (writes a temp file, then deletes it)\n")

    res = benchmark(vol["mount"], args.size, do_write, do_read)
    if "write" in res:
        print(f"  Write: {res['write']:7.0f} MB/s")
    if "read" in res:
        print(f"  Read:  {res['read']:7.0f} MB/s")

    print("\n" + verdict(label, practical, max(res.values()) if res else 0) + "\n")


if __name__ == "__main__":
    main()
