# usb-speed-check

A tiny macOS tool to check how fast a USB drive — and the **cable** connecting it — really is.

## The idea

You can't measure a cable on its own. A USB link runs at whatever speed the
**drive, cable and port negotiate together**, so the honest way to catch a bad
or old cable is to check two things:

1. **The negotiated link speed** — e.g. *USB 2.0 (480 Mb/s)* vs *USB 3.1 Gen 2
   (10 Gb/s)*. macOS reports this, and it's the reliable answer: a fast drive
   that negotiates a slow link is the classic sign of a weak cable or the wrong
   port. The tool always reports this and gives a plain-English verdict.
2. **The real throughput** — it times how fast it can **read** a large file
   that's already on the drive, to show the MB/s you actually get.

## Why it reads instead of writes

On macOS the OS caches writes in RAM, so a write test measures RAM (many GB/s),
not the drive — and on exFAT drives the only way to force a write to disk
(`fsync`) is painfully slow. Timing a **read** of a real, already-stored file
avoids all that, adds no drive wear, and is accurate as long as the file wasn't
just accessed. The tool also sanity-checks the number and discards it if it
looks like it came from the cache. **It only reads from the drive; it writes
nothing.**

## Requirements

- macOS (uses `system_profiler` and `diskutil`)
- The built-in Python 3 — no extra packages to install

## Usage

```bash
python3 usb_speed.py                 # pick from your plugged-in USB drives
python3 usb_speed.py "T7 Shield"     # test one by name
python3 usb_speed.py --size 2        # read up to 2 GiB when timing (default 1)
python3 usb_speed.py --no-throughput # just report the negotiated link speed
```

Example:

```
Drive:  T7 Shield  (/Volumes/T7 Shield)
Link:   USB 3.1 Gen 2 (10 Gb/s)

✅  The cable and port negotiated USB 3.1 Gen 2 (10 Gb/s) — a fast link, so
    the cable is not limiting you here.

    Real read throughput: 469 MB/s (~47% of this link's practical ceiling —
    normal for a drive like this).
```

## Notes

- Speeds are shown in MB/s (millions of bytes/second), the unit drive makers
  quote. A "10 Gb/s" link tops out around 1,000 MB/s in practice.
- The read test needs a file of at least 256 MiB on the drive. If there isn't
  one, throughput is skipped and the link speed is reported on its own.
- If a reading comes back implausibly high, it was served from the cache; the
  tool says so rather than reporting a fake number.
