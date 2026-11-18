# usb-speed-check

A tiny macOS tool to check how fast a USB drive — and the **cable** connecting it — really is.

## The idea

You can't measure a cable on its own. A USB link runs at whatever speed the
**drive, cable and port negotiate together**, so the honest way to catch a bad
or old cable is to check two things and compare them:

1. **The negotiated link speed** — e.g. *USB 2.0 (480 Mb/s)* vs *USB 3.1 Gen 2
   (10 Gb/s)*. macOS reports this. A fast drive that negotiates a slow link is
   the classic sign of a weak cable or the wrong port.
2. **The real throughput** — the tool writes a test file and reads it back,
   timing both, to show the MB/s you actually get.

It then gives a plain-English verdict: *is the cable the bottleneck or not?*

## Requirements

- macOS (uses `system_profiler` and `diskutil`)
- The built-in Python 3 — no extra packages to install

## Usage

```bash
python3 usb_speed.py                 # pick from your plugged-in USB drives
python3 usb_speed.py "T7 Shield"     # test one by name
python3 usb_speed.py --size 2        # 2 GiB test file (default is 1 GiB)
python3 usb_speed.py --read-only     # time reading only
python3 usb_speed.py --write-only    # time writing only
```

Example:

```
Drive:  T7 Shield  (/Volumes/T7 Shield)
Link:   USB 3.1 Gen 2 (10 Gb/s)
Test:   1 GiB  (writes a temp file, then deletes it)

  Write:     610 MB/s
  Read:      630 MB/s

✅  You're getting about 63% of this link's practical ceiling — that's
    normal. The cable is NOT the bottleneck.
```

## Notes

- The test file (`.usb_speed_test.bin`) is created on the drive and **deleted
  automatically** when the run finishes.
- The read test asks macOS to bypass its cache, so you measure the drive, not
  RAM.
- Speeds are shown in MB/s (millions of bytes/second), the same unit drive
  makers quote. A "10 Gb/s" link tops out around 1,000 MB/s in practice.
- It only reads from and writes to the drive you choose; it changes nothing
  else on your Mac.
