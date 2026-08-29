# Deploying the QHY camera + CFW filter wheel stack

Reconstructed on 2026-08-21 from the working install on **firefly-mk1**, by
inspecting what is actually on disk rather than from memory. The versions of what
is installed here were verified by checksum against their source tarballs, and the
recommended SDK (26.07.28) was downloaded and checked at the symbol level — see
[Provenance](#appendix-a--provenance-how-this-was-verified) for exactly what was
and was not tested.

Paths under `/opt/firefly/` are **site-internal notes on the reference machine and
are not part of this repository** — they are cited for provenance, not as things
you can open. Everything needed to deploy is in this file and the README.

Target configuration: a **QHY174GPS** camera with a **QHY CFW3** filter wheel
connected by the **4-pin CFW cable to the camera** (not a separate USB/serial
wheel). One Alpaca server process exposes both devices.

---

## 0. The thing that most likely broke the other machine

**`main` does not contain filter wheel support.** It has no `src/cfw_device.py`
and no `src/filter_wheel.py`. Deploying from `main` gives you a camera-only
server, and every `/api/v1/filterwheel/...` request 404s — regardless of what is
in `config.yaml`.

CFW support lives only on **`feature/cfw-support`**, which is branched off
`fix/qhy174gps-support` and therefore also carries the QHY174GPS fixes
(`sensor_bpp`, non-blocking `StartExposure`, GPS gating). Neither branch is
merged.

```bash
git clone git@github.com:lspitler/alpaca-qhyccd-camera.git
cd alpaca-qhyccd-camera
git checkout feature/cfw-support        # <-- REQUIRED for the filter wheel
```

Confirm before going further:

```bash
ls src/cfw_device.py src/filter_wheel.py   # both must exist
```

Branch layout as of this writing:

| Branch | Head | Has CFW? | Has 174GPS fixes? |
|---|---|:---:|:---:|
| `main` | `2739a4d` | ✘ | ✘ |
| `fix/qhy174gps-support` | `23dc81f` | ✘ | ✔ |
| `feature/cfw-support` | `5e239ee` | ✔ | ✔ |

---

## 1. Host prerequisites

```bash
sudo apt-get update
sudo apt-get install -y libusb-1.0-0 libudev1
```

`libqhyccd.so` links against exactly these, and nothing exotic:

```
libusb-1.0.so.0  libstdc++.so.6  libm.so.6  libgcc_s.so.1  libc.so.6  libudev.so.1
```

Its highest symbol-version requirements are `GLIBC_2.14` and `GLIBCXX_3.4.21`, so
it runs on any modern distro **and inside the `python:3.12-slim` (Debian trixie)
container even when the host is a much newer Ubuntu**. This is the opposite of the
`libfli` situation documented in `/opt/firefly/docker-compose.yaml`, where the
host-built object demanded `GLIBC_2.42` and had to be recompiled inside the image.
For QHY, bind-mounting the host's `.so` is fine — do not go looking for a rebuild
step, there isn't one.

Add yourself to `plugdev` if you are not already (`groups` to check). Not strictly
required given the udev `MODE="0666"` rule below, but it matches this box.

---

## 2. Install the QHYCCD SDK — use 26.07.28

Download the **Linux 64** tarball for release **26.07.28**:

- Changelog page: <https://www.qhyccd.com/html/prepub/log_en.html#!log_en.md#26.07.28>
- Direct: <https://www.qhyccd.com/file/repository/publish/SDK/260728/sdk_linux64_26.07.28.tar.gz>

That gives `libqhyccd.so.26.7.28.15`. Verified live on 2026-08-21: the link
downloads a 12,700,699-byte tarball dated 2026-07-28.

### Which version, and why this changed

This box runs **25.09.29** (`libqhyccd.so.25.9.29.10`), installed 2025-09-29, and
that is the build all the hardware testing in this repo was done against. But it
is **not listed on the public changelog page at all** — the page jumps from
26.06.04 straight back to 25.03.24. So a fresh machine cannot download 25.09.29;
it can only be copied off a machine that already has it:

```bash
scp <user>@<host-with-the-tarball>:'~/Downloads/sdk_linux64_25.09.29.tgz' .   # known-good fallback
```

Keep that as the fallback, but **install 26.07.28 on a new machine**, because:

- **26.06.04 is struck through on the changelog page** — it renders as
  `~~26.06.04~~`, i.e. withdrawn. Its tarball also ships **no install scripts
  whatsoever** (`tar tzf ... | grep -c '\.sh$'` → 0, only `etc/ lib/ sbin/ usr/`),
  so there is literally nothing to run. It was downloaded here 2026-06-13 and
  never installed; that instinct was right.
- **`qhyccdsdk-v2.0.11`** was tried and superseded on 2026-06-13. Only stale
  firmware in `/usr/local/lib/qhy/firmware/` remains from it. Second dead end.
- 25.09.29 is undownloadable, so recommending it strands anyone without ssh
  access to this box.

**What was actually checked on 26.07.28** (2026-08-21, symbol level — the tarball
was downloaded and unpacked to `/tmp`, no hardware run):

| Check | Result |
|---|---|
| All 31 SDK functions this driver calls | present, and `DF .text` (real definitions, not `*UND*`) |
| The three CFW entry points | `IsQHYCCDCFWPlugged`, `GetQHYCCDCFWStatus`, `SendOrder2QHYCCDCFW` all defined |
| Shared library dependencies | identical six: `libusb-1.0.so.0`, `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6`, `libudev.so.1` |
| Symbol version floors | `GLIBC_2.14` / `GLIBCXX_3.4.21` — same as 25.09.29, so §1 and the Docker bind-mount story are unchanged |
| Install scripts | `install.sh` and `uninstall.sh` present; **`distclean.sh` is gone** |
| Firmware images | 111 (25.09.29 had 109) |
| udev rules | 18008 bytes (25.09.29: 17349) |

Total exported symbol count dropped from 6964 to 6173, so QHY did remove things —
just nothing this driver calls. It has **not** been exercised against the camera
or the wheel, so run §5 before trusting it on sky, and be ready to fall back.

### Install it

The SDK ships no packaging. **`install.sh` is just a pile of `cp` commands and
must run as root from inside the extracted directory** (it uses relative source
paths, so `cd` in first or it silently copies nothing):

```bash
tar xzf sdk_linux64_26.07.28.tar.gz
cd sdk_linux64_26.07.28
sudo ./install.sh
```

That places:

| Destination | Contents |
|---|---|
| `/usr/local/lib/libqhyccd.so{,.20,.26.7.28.15}` | the shared library + symlinks |
| `/usr/local/lib/libqhyccd.a` | static lib (unused by us) |
| `/usr/local/include/qhyccd*.h` | headers (unused by us — we use ctypes) |
| `/lib/firmware/qhy/` | 111 camera firmware images |
| `/etc/udev/rules.d/85-qhyccd.rules`, `/lib/udev/rules.d/` | permissions + FX3 firmware loading |
| `/sbin/fxload`, `/usr/share/usb/a3load.hex` | FX3 firmware loader |
| `/usr/local/{testapp,doc,cmake_modules,fx3load,riffa_linux_driver,udev}` | samples/docs |

`install.sh` ends with `ldconfig`. Reload udev and replug the camera:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
# then physically unplug/replug the camera
```

### If a previous/wrong SDK is already on the target

26.07.28 ships `uninstall.sh`, which removes this version's files. The aggressive
`distclean.sh` — which hunts down `libqhyccd*` and QHY firmware **of any version**
anywhere under `/lib`, `/usr/lib` and `/usr/local` — **only exists in the 25.09.29
tarball**, so keep that tarball around for it. Use it if the machine has been
through several SDK versions (as this box had):

```bash
cd sdk_linux64_25.09.29
sudo ./distclean.sh                # wipes ALL libqhyccd + QHY firmware, any version
cd ../sdk_linux64_26.07.28
sudo ./install.sh
```

Note `distclean.sh` also deletes `/sbin/fxload` and the udev rules, so always
follow it with `install.sh` and a udev reload.

### Verify the SDK install

```bash
ls -l /usr/local/lib/libqhyccd.so*
# libqhyccd.so -> libqhyccd.so.20 -> libqhyccd.so.26.7.28.15
ldconfig -p | grep qhyccd
ldd /usr/local/lib/libqhyccd.so           # no "not found" lines
ls /lib/firmware/qhy | wc -l              # 111 (26.07.28) / 109 (25.09.29)
lsusb -d 1618:                            # 1618:c175 for QHY174/GPS
ls -l /dev/bus/usb/*/*                    # QHY node should be mode 0666
```

The permissions come from a **vendor-wide** rule at the end of
`85-qhyccd.rules` — `ATTRS{idVendor}=="1618", MODE="0666"` — so every QHY device
is covered without a per-model entry. There is deliberately **no `c175` rule** in
any SDK version: the QHY174GPS boots with its firmware already in flash and needs
no `fxload` step. If instead your camera appears under a *loader* product ID and
disappears, that is the FX3 path and depends on `/sbin/fxload` +
`/lib/firmware/qhy/` being present.

---

## 3. Deploy the server

Two options. **Docker is what runs here** and is recommended.

### 3a. Docker (as deployed on firefly-mk1)

```bash
cd /opt/alpaca-qhyccd-camera        # on feature/cfw-support
docker build -t alpaca-qhyccd-camera .
```

The container needs `privileged: true` **and** `/dev/bus/usb` bind-mounted.
libqhyccd talks to the camera through libusb/usbfs, not through a char device, so
a narrow `devices:` mapping is *not* sufficient here. (Again the opposite of the
FLI server, which reaches its camera via `/dev/fliusb0` and needs neither.)

Compose service, as it actually ran (`firefly-qhyccd-camera-1`, project
`firefly`) — with the one edit that the versioned library mount below reads
`26.7.28.15` to match §2's recommended SDK, where the run here used
`25.9.29.10`:

```yaml
  qhyccd-camera:
    build: /opt/alpaca-qhyccd-camera
    image: alpaca-qhyccd-camera
    pull_policy: never
    privileged: true
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./qhyccd-config.yaml:/alpyca/config.yaml:ro
      - /dev/bus/usb:/dev/bus/usb
      - /usr/local/lib/libqhyccd.so:/usr/local/lib/libqhyccd.so:ro
      - /usr/local/lib/libqhyccd.so.20:/usr/local/lib/libqhyccd.so.20:ro
      - /usr/local/lib/libqhyccd.so.26.7.28.15:/usr/local/lib/libqhyccd.so.26.7.28.15:ro
```

> **Copy-paste hazard:** the third mount is *version-specific*. If the target
> machine has a different SDK build, that path does not exist, Docker either
> creates an empty directory in its place or fails, and `dlopen` then dies at
> connect time with a confusing error. Check with `ls -l /usr/local/lib/libqhyccd.so*`
> and edit the line to match.
>
> All three files must be mounted individually. Mounting the whole
> `/usr/local/lib` directory instead is tempting but **breaks the image** — it
> shadows the container's own Python installation, which lives there. (If you
> want to be version-agnostic, mount the three files under a fresh path such as
> `/qhy/` and point `library:` at `/qhy/libqhyccd.so`, the way `fli-config.yaml`
> uses `/libfli/`. That is a change from what shipped here, so test it.)

```bash
docker compose up -d --build qhyccd-camera
docker compose logs -f qhyccd-camera
```

### 3b. Bare metal

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt      # fastapi, uvicorn, pydantic, pyyaml,
                                     # loguru, astropy, numpy, alpyca
python src/main.py                   # NB: src/main.py, not main.py
```

The README's `python main.py` is stale — the entrypoint moved into `src/`, which
is what the Dockerfile's `CMD ["python", "src/main.py"]` uses.

---

## 4. Configuration

The repo's `config.yaml` (port **5900**) and the deployed
`/opt/firefly/qhyccd-config.yaml` (port **5800**) differ only in port and filter
names. Pick one port and keep the clients consistent — the watchdog and the
`/opt/firefly` stack assume 5800; the loose test scripts in `~/Downloads` assume
5900.

```yaml
entity: qhyccd_camera
library: /usr/local/lib/libqhyccd.so     # the symlink, not the versioned file
server:
  host: 0.0.0.0
  port: 5800
log_level: INFO
devices:
  - entity: QHY174GPS
    device_number: 0
    sensor_bpp: 12      # native ADC depth; pixels right-shifted from 16-bit to true ADU
    defaults:
      temperature: -50
      readout_mode: 0
      binning: 1
      gain: 0
      offset: 0
      usb_traffic: 10
filter_wheel:
  entity: QHYFilterWheel
  device_number: 0
  names:                # length of this list DEFINES the slot count
    - B
    - G
    - R
    - L
    - Sloan-u
    - Sloan-i
    - Sloan-z
  focus_offsets:        # must be the SAME length as names
    - 0
    - 0
    - 0
    - 0
    - 0
    - 0
    - 0
  timeout: 60           # seconds allowed for one move
```

Notes that matter:

- `sensor_bpp: 12` is **required** for the QHY174GPS. Without it, pixel values are
  left as 16-bit-scaled and read ~16× too high.
- The `filter_wheel:` block is what registers the wheel. Omit it entirely and the
  server behaves exactly as before — the router is never mounted, no code paths
  change.
- `len(names)` is the authoritative slot count and is used to validate positions
  *and* to disambiguate the SDK's position encoding (see §6). Getting it wrong
  causes misread positions, not just a bad label. `focus_offsets` must match its
  length.
- `library:` should point at the **unversioned symlink**, so an SDK change doesn't
  require a config edit.

---

## 5. Bring-up and test

**Connect order is mandatory: camera first, then filter wheel.** The CFW device
borrows the camera's SDK handle (`CFWDevice._handle` returns
`camera_device.handle`) — there is no independent connection. The code is
forgiving about this in two ways: `connect()` will auto-connect the camera if it
is not connected, and (as of `7717174`) it waits up to 30 s for an in-progress
camera connect rather than failing immediately. Do not rely on that; connect
explicitly and poll.

Connect is **asynchronous** per the ASCOM contract — `PUT connected` returns
immediately and you must poll `GET connected`:

```bash
BASE=http://localhost:5800/api/v1

curl -X PUT $BASE/camera/0/connected -d "Connected=true&ClientID=1&ClientTransactionID=1"
for i in $(seq 1 30); do
  curl -s "$BASE/camera/0/connected?ClientID=1&ClientTransactionID=1" | grep -q '"Value":true' && { echo "camera up"; break; }
  sleep 1
done

curl -X PUT $BASE/filterwheel/0/connected -d "Connected=true&ClientID=1&ClientTransactionID=1"
curl -s "$BASE/filterwheel/0/names?ClientID=1&ClientTransactionID=1"
curl -s "$BASE/filterwheel/0/position?ClientID=1&ClientTransactionID=1"

# move to slot 2, then poll until position stops reporting -1
curl -X PUT $BASE/filterwheel/0/position -d "Position=2&ClientID=1&ClientTransactionID=1"
for i in $(seq 1 20); do sleep 1; curl -s "$BASE/filterwheel/0/position?ClientID=1&ClientTransactionID=1"; echo; done
```

`GET position` returns **-1 while moving** — that is correct ASCOM behaviour, not
an error. Positions are **0-based**.

Ready-made scripts exist on this box and are worth copying alongside the SDK
tarball (they target port 5900 — edit if you use 5800):

- `~/Downloads/test-camera.sh` — connect, read sensor info, one 1 s exposure
- `~/Downloads/test-filterwheel.bash` — full camera-then-wheel connect and a move
- `~/Downloads/test_qhy_nogps.py` — asserts the `TIME-SRC: SYSCLOCK` fallback
- `python tests/test.py` in-repo — connect, expose, write FITS
- `python tests/test_conformu.py` in-repo — ASCOM ConformU validation
  (`~/Downloads/conformu.linux-x64.tar.xz` is the runner)

Sanity check that both devices are advertised:

```bash
curl -s "http://localhost:5800/management/v1/configureddevices?ClientID=1&ClientTransactionID=1"
```

Both a `Camera` and a `FilterWheel` entry must appear. If only the camera does,
you are on the wrong branch — go back to §0.

---

## 6. Known issues and gotchas

**No filterwheel endpoints / 404.** Wrong branch. See §0.

**`dlopen` / `OSError` at connect, not at startup.** The library is loaded lazily
by ctypes on first connect, so a bad `library:` path or a missing bind mount looks
like a healthy server that fails only when a client arrives. Check the versioned
mount path.

**Wheel not detected — "No filter wheel detected (IsQHYCCDCFWPlugged failed)".**
The 4-pin CFW cable is not seated, the wheel has no power, or the camera handle
isn't actually open yet. This is a genuine hardware/cable check, not a config one.

**Position reads look wrong.** `GetQHYCCDCFWStatus` is inconsistent across QHY
firmware: some builds return the raw position **byte value** (`0x00`, `0x01`, …),
others an **ASCII digit** (`'0'` = `0x30`). `_read_position()` handles both, and it
disambiguates using `len(names)` — so a wrong slot count in config will make
correct hardware read incorrectly.

**A move that never completes.** `_wait_for_move` deliberately requires seeing a
*transition* before accepting the target, because the SDK briefly reports the old
position before the wheel physically starts. It sleeps 0.3 s first and then polls
to `timeout` (60 s). A wheel that is slower than `timeout` needs the config bumped,
not a code change.

**GPS precise timing is not available on the QHY174 — this is settled, do not
re-debug it.** `GetQHYCCDPreciseExposureInfo` and
`GetQHYCCDRollingShutterEndOffset` log "not supported" and always will. Root cause
confirmed 2026-06-29 by symbol scan: the SDK is class-based, and the QHY174's
class never overrides them — only the weak `QHYBASE` stubs exist. It is **not** a
firmware-revision problem and **not** fixable by a different build of this SDK
version. Timestamps fall back to `TIME-SRC: SYSCLOCK`. Full analysis in
`/opt/firefly/CAMERA_GPS_TIMING.md`. This does not block imaging — do not gate a
collect on GPS timing.

**The server can wedge inside the SDK.** Observed: container `Up 12 hours`, port
5800 accepting TCP, HTTP never responding, logs frozen — a blocking call stuck in
libqhyccd/USB. Docker reports the container healthy throughout, and a downstream
sensorkit Alpaca service crash-looped on it. `docker restart` clears it instantly.
A watchdog on this box automates the recovery; if the target machine is
unattended, port it:

- `/etc/systemd/system/qhyccd-camera-watchdog.service` (oneshot)
- `/etc/systemd/system/qhyccd-camera-watchdog.timer` (`OnBootSec=2min`,
  `OnUnitActiveSec=5min`)
- `/usr/local/bin/qhyccd-camera-watchdog.sh`

Every 5 minutes it gates on `lsusb -d 1618:` (quiet exit on machines with no QHY
hardware), checks the container is running (it will **not** start a stopped one,
to avoid fighting a manual `docker compose up`), probes
`/management/apiversions` with a 5 s timeout, and `docker restart`s only if that
probe fails. Background in `/opt/firefly/qhy_sensor_watchdog_service_details.md`.

**Full-frame readout crash/hang.** Separately investigated around
`GetQHYCCDSingleFrame`; leading hypothesis is USB power/cable/hub instability
(check `sudo dmesg | grep -iE "usb|xhci|1618"` for resets). See
`/opt/firefly/CAMERA_READOUT_FAULT.md`. `usbfs_memory_mb` is at the stock **16**
on this box and was never raised — fine for the 174's 1920×1200 frames, but a
QHY600-class sensor may need `usbcore.usbfs_memory_mb=1000`.

**Not this repo:** `~/Desktop/alpaca-qhyccd-filter-wheel` is the *standalone*
server for a wheel on its own serial/USB connection. For a CFW cabled to the
camera it is the wrong tool — that is the whole point of the CFW support here, and
running both would contend for the wheel.

---

## Appendix A — Provenance (how this was verified)

Not from memory. On firefly-mk1, 2026-08-21:

- **SDK 26.07.28** (the §2 recommendation): downloaded from the changelog link on
  2026-08-21 (12,700,699 bytes, mtime 2026-07-28), unpacked to `/tmp`, and checked
  with `objdump -T` / `ldd` against the 31 SDK symbols grepped out of `src/*.py`.
  All present as `DF .text`; deps and `GLIBC_2.14`/`GLIBCXX_3.4.21` floors match
  25.09.29. `tar tzf` confirmed 26.06.04 contains zero `.sh` files and that
  26.07.28 has `install.sh`/`uninstall.sh` but no `distclean.sh`. The `~~26.06.04~~`
  strikethrough was read from the page's own source, `log_en.md`. **Not run against
  hardware.**
- `md5sum` of the installed `libqhyccd.so.25.9.29.10` (this box's 25.09.29), all four
  `/usr/local/include/qhyccd*.h`, and a sample of `/lib/firmware/qhy/*.img`
  (`QHY174.img`, `Origin678C.img`, `QHY1253.img`) — **all match**
  `~/Downloads/sdk_linux64_25.09.29/` byte for byte. Firmware file count matches
  at 109.
- `docker inspect firefly-qhyccd-camera-1` (still present, exited 7 weeks ago) —
  the source for §3a's `privileged`, `network_mode: host`, and the exact five bind
  mounts. The one deviation from what that container ran: the versioned library
  mount is written as `26.7.28.15` to match §2, not the `25.9.29.10` it used.
- `ldd` and `objdump -T` on the installed `.so` — the dependency list and the
  `GLIBC_2.14` / `GLIBCXX_3.4.21` floors in §1.
- `git ls-tree origin/main src/` — proof that `main` lacks `cfw_device.py` and
  `filter_wheel.py`.
- `/opt/firefly/qhyccd-config.yaml`, `CAMERA_GPS_TIMING.md`,
  `CAMERA_READOUT_FAULT.md`, `qhy_sensor_watchdog_service_details.md`, and the two
  systemd watchdog units.

One discrepancy, called out honestly: the installed
`/etc/udev/rules.d/85-qhyccd.rules` (17192 bytes, dated 2025-03-14) matches
**none** of the tarballs' copies — 25.09.29's is 17349 bytes, 26.06.04's 17479,
26.07.28's 18008 — it predates them all and survives from an earlier SDK install.
It works because the vendor-wide `MODE="0666"` rule is present in every version.
Following §2 installs 26.07.28's rules, which are the largest and a superset; that
is the recommended path and is not expected to change behaviour.

Also leftover and harmless: `/usr/local/lib/qhy/firmware/` from the abandoned
`qhyccdsdk-v2.0.11` attempt. Nothing reads it — the udev rules load firmware from
`/lib/firmware/qhy/`. `distclean.sh` removes it if you want the machine clean.
