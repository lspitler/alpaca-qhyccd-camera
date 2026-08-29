# ASCOM Alpaca Server for QHYCCD cameras and CFW filter wheels (libqhyccd)

A FastAPI-based server implementing the ASCOM **ICameraV4** interface, and
optionally **IFilterWheelV3** for a QHY **CFW** filter wheel connected to the
camera by the 4-pin CFW cable. Both devices are served by one process.
Communication is via the published QHYCCD SDK (`libqhyccd`). Hardware testing
was done against SDK **25.09.29**; **26.07.28** is the current release and the
recommended starting point for a new install (see
[Installing the QHYCCD SDK](#installing-the-qhyccd-sdk)).

Hardware tested:

| Device | Notes |
|---|---|
| QHY600M | USB only (no PCIe support) |
| QHY174GPS | Requires `sensor_bpp: 12`; precise GPS timing is unavailable in the SDK for this model (falls back to system clock) |
| QHY CFW3 | Via 4-pin CFW cable to the camera, **not** a separate USB/serial wheel |

> **Deploying to a new machine?** `DEPLOY.md` is the step-by-step,
> verified-against-a-working-install runbook, including SDK version pitfalls,
> the Docker bind-mount hazards, and known failure modes. Read it alongside
> this file.

---

## Implemented ICameraV4 capabilities as of this driver version

| Capability           | Supported |
|----------------------|-----------|
| BayerOffsetX         | ✘         |
| BayerOffsetY         | ✘         |
| BinX                 | ✔         |
| BinY                 | ✔         |
| CameraState          | ✔         |
| CameraXSize          | ✔         |
| CameraYSize          | ✔         |
| CanAbortExposure     | ✔         |
| CanAsymmetricBin     | ✘         |
| CanFastReadout       | ✘         |
| CanGetCoolerPower    | ✔         |
| CanPulseGuide        | ✘         |
| CanSetCCDTemperature | ✔         |
| CanStopExposure      | ✘         |
| CCDTemperature       | ✔         |
| CoolerOn             | ✔         |
| CoolerPower          | ✔         |
| ElectronsPerADU      | ✘         |
| ExposureMax          | ✔         |
| ExposureMin          | ✔         |
| ExposureResolution   | ✔         |
| FastReadout          | ✘         |
| FullWellCapacity     | ✔         |
| Gain                 | ✔         |
| GainMax              | ✔         |
| GainMin              | ✔         |
| Gains                | ✘         |
| HasShutter           | ✘         |
| HeatSinkTemperature  | ✘         |
| ImageArray           | ✔         |
| ImageReady           | ✔         |
| IsPulseGuiding       | ✘         |
| LastExposureDuration | ✔         |
| MaxADU               | ✔         |
| MaxBinX              | ✔         |
| MaxBinY              | ✔         |
| NumX                 | ✔         |
| NumY                 | ✔         |
| Offset               | ✔         |
| OffsetMax            | ✔         |
| OffsetMin            | ✔         |
| Offsets              | ✘         |
| PercentCompleted     | ✘         |
| PixelSizeX           | ✔         |
| PixelSizeY           | ✔         |
| ReadoutMode          | ✔         |
| ReadoutModes         | ✔         |
| SensorName           | ✔         |
| SensorType           | ✔         |
| SetCCDTemperature    | ✔         |
| StartX               | ✔         |
| StartY               | ✔         |
| SubExposureDuration  | ✘         |
| AbortExposure        | ✔         |
| PulseGuide           | ✘         |
| StartExposure        | ✔         |
| StopExposure         | ✘         |

`StartExposure` is **non-blocking** per the ASCOM async contract — it returns
immediately and the client polls `ImageReady`.

Beyond the standard interface, `GET /api/v1/camera/{n}/gpsmetadata` returns the
per-frame GPS status for GPS-capable cameras.

## Implemented IFilterWheelV3 capabilities

Present only when a `filter_wheel:` block exists in `config.yaml`.

| Capability    | Supported | Notes |
|---------------|-----------|-------|
| FocusOffsets  | ✔         | From config; must be the same length as `Names` |
| Names         | ✔         | From config; `len(names)` **defines the slot count** |
| Position      | ✔         | 0-based. Returns **-1 while moving** (correct ASCOM behaviour) |

---

## Architecture

| File                  | Purpose                                     |
|-----------------------|---------------------------------------------|
| `src/main.py`         | FastAPI app, lifespan, router wiring        |
| `src/config.py`       | Pydantic config models, YAML loader         |
| `config.yaml`         | User-editable configuration                 |
| `src/camera.py`       | FastAPI router – ICameraV4 endpoints        |
| `src/camera_device.py`| Low-level libqhyccd camera driver           |
| `src/filter_wheel.py` | FastAPI router – IFilterWheelV3 endpoints   |
| `src/cfw_device.py`   | Low-level CFW driver (shares camera handle) |
| `src/libqhyccd.py`    | Wrappers to libqhyccd library               |
| `src/management.py`   | `/management` Alpaca management endpoints   |
| `src/setup.py`        | `/setup` HTML stub pages                    |
| `src/discovery.py`    | UDP Alpaca discovery responder (port 32227) |
| `src/responses.py`    | Pydantic response models                    |
| `src/exceptions.py`   | ASCOM Alpaca error classes                  |
| `src/shr.py`          | Shared FastAPI dependencies / helpers       |
| `src/log.py`          | Loguru config + stdlib intercept handler    |
| `tests/test.py`       | Quick smoke-test script                     |
| `tests/test_conformu.py` | ConformU ASCOM validation runner         |
| `requirements.txt`    | Python package dependencies                 |
| `Dockerfile`          | Container build                             |

---

## Installing the QHYCCD SDK

The SDK is **not** bundled and must be on the host before the server can talk to
any camera. Download the **Linux 64** tarball for release **26.07.28** from the
QHYCCD SDK changelog page:

- Page: <https://www.qhyccd.com/html/prepub/log_en.html#!log_en.md#26.07.28>
- Direct: <https://www.qhyccd.com/file/repository/publish/SDK/260728/sdk_linux64_26.07.28.tar.gz>

which gives `libqhyccd.so.26.7.28.15`.

> **Skip 26.06.04 — it is struck through on that page** (listed as
> `~~26.06.04~~`, i.e. withdrawn), and its tarball ships **no `install.sh` at
> all**, so there is nothing to run. `qhyccdsdk-v2.0.11` is a separate dead end.
> Go straight to 26.07.28.

**Version status, stated plainly.** This driver's hardware testing was done
against **25.09.29** (`libqhyccd.so.25.9.29.10`), which is *not* listed on the
public changelog page — you can only get it by copying the tarball from a machine
that already has it. 26.07.28 has been checked at the ABI level and is a safe
starting point: all 31 SDK functions this driver calls are present and defined in
it (including the three CFW entry points `IsQHYCCDCFWPlugged`,
`GetQHYCCDCFWStatus`, `SendOrder2QHYCCDCFW`), it links against the same six
shared libraries, and it has the same `GLIBC_2.14` / `GLIBCXX_3.4.21` floors. It
has **not** been run against hardware here, so exercise the smoke tests below
before a night of observing, and keep a 25.09.29 tarball as the known-good
fallback.

System prerequisites — `libqhyccd.so` needs nothing exotic:

```bash
sudo apt-get update
sudo apt-get install -y libusb-1.0-0 libudev1
```

The SDK ships no packaging. `install.sh` is a pile of `cp` commands using
**relative** source paths, so it must run as root **from inside** the extracted
directory or it silently copies nothing:

```bash
tar xzf sdk_linux64_26.07.28.tar.gz
cd sdk_linux64_26.07.28
sudo ./install.sh                  # ends with ldconfig
```

It installs:

| Destination | Contents |
|---|---|
| `/usr/local/lib/libqhyccd.so{,.20,.26.7.28.15}` | shared library + symlinks |
| `/usr/local/include/qhyccd*.h` | headers (unused — this driver uses ctypes) |
| `/lib/firmware/qhy/` | camera firmware images (111 in 26.07.28, 109 in 25.09.29) |
| `/etc/udev/rules.d/85-qhyccd.rules`, `/lib/udev/rules.d/` | permissions + FX3 firmware loading |
| `/sbin/fxload`, `/usr/share/usb/a3load.hex` | FX3 firmware loader |

Reload udev, then **physically unplug and replug the camera**:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Device permissions come from a vendor-wide rule at the end of
`85-qhyccd.rules` (`ATTRS{idVendor}=="1618", MODE="0666"`), so every QHY model is
covered without a per-model entry.

If the target has been through several SDK versions, clean first. 26.07.28 ships
only `install.sh` and `uninstall.sh`; the aggressive `distclean.sh` — which
removes `libqhyccd*` and QHY firmware of **any** version from `/lib`, `/usr/lib`
and `/usr/local` — exists only in the older 25.09.29 tarball, so keep a copy of
it. It also deletes `/sbin/fxload` and the udev rules, so always follow it with
`install.sh` and a udev reload:

```bash
cd sdk_linux64_25.09.29   && sudo ./distclean.sh
cd ../sdk_linux64_26.07.28 && sudo ./install.sh
```

Verify:

```bash
ls -l /usr/local/lib/libqhyccd.so*   # .so -> .so.20 -> .so.26.7.28.15
ldconfig -p | grep qhyccd
ldd /usr/local/lib/libqhyccd.so      # no "not found" lines
ls /lib/firmware/qhy | wc -l         # 111 on 26.07.28
lsusb -d 1618:                       # camera present (1618:c175 = QHY174/GPS)
```

---

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit to match your setup:

- `library`: path to `libqhyccd.so` — use the **unversioned symlink** so an SDK
  change doesn't require a config edit
- `server.host` / `server.port`: listen address (code default is 5000; the
  shipped `config.yaml` uses 5900)
- `devices[].sensor_bpp`: native ADC bit depth of the sensor — **12** for
  QHY174GPS, 14 for QHY268C, 16 for QHY600 (the default). The SDK always
  delivers 16-bit data; pixels are right-shifted by `16 - sensor_bpp` to give
  true ADU. Getting this wrong makes pixel values read ~16× too high.
- `devices[].defaults`: default temperature, readout mode, binning, gain,
  offset, USB traffic

Camera properties (sensor size, pixel size, gain/offset ranges, exposure limits)
are **queried from the SDK at connection time** — no hardcoding required.

Multiple QHYCCD cameras can be registered by adding further entries under
`devices:` with distinct `device_number` values.

### Filter wheel

Add an optional `filter_wheel:` block. Omit it and the router is never mounted —
no code paths change and the server behaves exactly as a camera-only build.

```yaml
filter_wheel:
  entity: QHYFilterWheel
  device_number: 0
  names:                # length of this list DEFINES the slot count
    - Sloan-u
    - Sloan-g
    - Sloan-r
    - Sloan-i
    - Sloan-z
    - Luminance
  focus_offsets:        # must be the SAME length as names
    - 0
    - 0
    - 0
    - 0
    - 0
    - 0
  timeout: 60           # seconds allowed for one move
```

- `len(names)` is the authoritative slot count. It validates positions **and**
  disambiguates the SDK's position encoding — `GetQHYCCDCFWStatus` returns a raw
  byte on some QHY firmware and an ASCII digit on others, and a wrong slot count
  makes correct hardware read incorrectly.
- `focus_offsets` must match `names` in length.
- A wheel slower than `timeout` needs the config bumped, not a code change.

---

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

### Connect order is mandatory: camera first, then filter wheel

The CFW has no independent connection — it borrows the camera's SDK handle. The
code will auto-connect the camera and waits up to 30 s for an in-progress camera
connect, but don't rely on that: connect the camera explicitly and poll.

Connect is **asynchronous** per the ASCOM contract — `PUT connected` returns
immediately, so poll `GET connected`:

```bash
BASE=http://localhost:5900/api/v1

curl -X PUT $BASE/camera/0/connected -d "Connected=true&ClientID=1&ClientTransactionID=1"
for i in $(seq 1 30); do
  curl -s "$BASE/camera/0/connected?ClientID=1&ClientTransactionID=1" | grep -q '"Value":true' && { echo "camera up"; break; }
  sleep 1
done

curl -X PUT $BASE/filterwheel/0/connected -d "Connected=true&ClientID=1&ClientTransactionID=1"
curl -s "$BASE/filterwheel/0/names?ClientID=1&ClientTransactionID=1"
curl -X PUT $BASE/filterwheel/0/position -d "Position=2&ClientID=1&ClientTransactionID=1"
# poll until position stops reporting -1
curl -s "$BASE/filterwheel/0/position?ClientID=1&ClientTransactionID=1"
```

Confirm both devices are advertised — a `Camera` **and** a `FilterWheel` entry
must appear:

```bash
curl -s "http://localhost:5900/management/v1/configureddevices?ClientID=1&ClientTransactionID=1"
```

---

## Smoke test

```bash
# Requires hardware connected, i.e. will operate camera
python tests/test.py
```

---

## Docker

```bash
docker build -t alpaca-qhyccd-camera .
docker run -d --name alpaca-qhyccd-camera \
    -v ./config.yaml:/alpyca/config.yaml:ro \
    --privileged -v /dev/bus/usb:/dev/bus/usb \
    -v /usr/local/lib/libqhyccd.so:/usr/local/lib/libqhyccd.so:ro \
    -v /usr/local/lib/libqhyccd.so.20:/usr/local/lib/libqhyccd.so.20:ro \
    -v /usr/local/lib/libqhyccd.so.26.7.28.15:/usr/local/lib/libqhyccd.so.26.7.28.15:ro \
    --network host \
    --restart unless-stopped \
    alpaca-qhyccd-camera
docker logs -f alpaca-qhyccd-camera
```

`--privileged` **and** the `/dev/bus/usb` mount are both required: libqhyccd
talks to the camera through libusb/usbfs, not a char device, so a narrow
`--device` mapping is not sufficient.

> **Copy-paste hazards.**
> The third library mount is **version-specific**. If the host has a different
> SDK build that path does not exist, Docker puts an empty directory in its
> place, and `dlopen` then fails at *connect* time — not at startup — with a
> confusing error. Check `ls -l /usr/local/lib/libqhyccd.so*` and edit the line
> to match.
>
> Mount the three library files **individually**. Mounting the whole
> `/usr/local/lib` directory instead breaks the image — it shadows the
> container's own Python installation, which lives there.

The host SDK library runs fine inside `python:3.12-slim`: its highest symbol
requirements are `GLIBC_2.14` and `GLIBCXX_3.4.21`, so bind-mounting the host's
`.so` needs no rebuild.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/api/v1/filterwheel/...` 404s | No `filter_wheel:` block in `config.yaml`, or the deployed code lacks `src/cfw_device.py` and `src/filter_wheel.py` |
| `OSError` / `dlopen` failure at connect, not startup | ctypes loads the library lazily on first connect — bad `library:` path or a missing/incorrect Docker bind mount |
| "No filter wheel detected (IsQHYCCDCFWPlugged failed)" | 4-pin CFW cable not seated, wheel unpowered, or camera handle not open yet. Genuine hardware check, not config |
| Filter positions read wrong | Slot count (`len(names)`) doesn't match the wheel — the position decoder uses it to disambiguate byte vs ASCII encoding |
| Pixel values ~16× too high | `sensor_bpp` not set for a sub-16-bit sensor |
| Server accepts TCP but never answers HTTP; logs frozen | A blocking call wedged inside libqhyccd/USB. Restart the process/container; see `DEPLOY.md` for a watchdog |
| GPS precise timing unavailable on QHY174 | Settled and not fixable — the SDK's QHY174 class never overrides the timing calls. Falls back to system clock. Do not re-debug |

---

## ASCOM Conformance

<!-- conformu:start -->
Last tested with **ConformU 4.3.0 (Build 49708.0503dc7)** on 2026-05-16
(`python test_conformu.py`):

| Device | Errors | Issues | Info | Status |
|--------|:------:|:------:|:----:|:------:|
| QHY600M_1 (Camera #0) | 1 | 0 | 263 | ✓ PASS |

_Errors may be non-zero when no hardware is attached (NotConnectedException is the expected response). **Issues == 0** indicates Alpaca protocol conformance._
<!-- conformu:end -->

Re-run with `python tests/test_conformu.py`. It tests every device in
`configureddevices`, so a `FilterWheel` row appears in the table above once
re-run on a machine with the wheel attached.
