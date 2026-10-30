"""Offline checks of the SDK threading rules — no camera or SDK needed.

A fake libqhyccd records which thread runs each call and can block a call to
simulate the wedged firmware seen on jetson008 (GetQHYCCDSingleFrame never returns).

Run from the repo root (needs the requirements.txt packages plus pytest):
    python -m pytest tests/test_sdk_threading.py
"""
import sys
import threading
import time
from ctypes import c_uint32
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import camera_device
from camera_device import CameraDevice, CameraState, SdkWedgedError
from config import DeviceConfig
from libqhyccd import QHY_CONTROL, QHY_SUCCESS

W, H = 64, 48


class FakeLib:
    """Stands in for the CDLL. Sets out-params through ctypes byref objects."""

    def __init__(self):
        self.calls = []  # (name, thread name)
        self.block = {}  # name -> Event the call waits on before returning
        self.overlap = False
        self._active = 0
        self._mutex = threading.Lock()

    def _enter(self, name):
        with self._mutex:
            self._active += 1
            if self._active > 1 and name != "CancelQHYCCDExposingAndReadout":
                self.overlap = True
            self.calls.append((name, threading.current_thread().name))
        if name in self.block:
            self.block[name].wait()

    def _leave(self):
        with self._mutex:
            self._active -= 1

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def fn(*args):
            self._enter(name)
            try:
                return getattr(self, "_" + name, lambda *a: QHY_SUCCESS)(*args)
            finally:
                self._leave()

        fn.__name__ = name
        return fn

    @staticmethod
    def _set(ref, value):
        ref._obj.value = value

    def _ScanQHYCCD(self):
        return 1

    def _GetQHYCCDId(self, index, buf):
        buf.value = b"QHY174M-test"
        return QHY_SUCCESS

    def _OpenQHYCCD(self, buf):
        return c_uint32(1)

    def _GetQHYCCDChipInfo(self, h, cw, ch, iw, ih, pw, ph, bpp):
        for ref, v in ((cw, 11.3), (ch, 7.1), (iw, W), (ih, H), (pw, 5.86), (ph, 5.86), (bpp, 16)):
            self._set(ref, v)
        return QHY_SUCCESS

    def _GetQHYCCDParamMinMaxStep(self, h, control, lo, hi, step):
        lo_v, hi_v, st_v = (1.0, 3.6e9, 1.0) if control == QHY_CONTROL.EXPOSURE else (0.0, 100.0, 1.0)
        self._set(lo, lo_v), self._set(hi, hi_v), self._set(step, st_v)
        return QHY_SUCCESS

    def _GetQHYCCDNumberOfReadModes(self, h, n):
        self._set(n, 1)
        return QHY_SUCCESS

    def _IsQHYCCDControlAvailable(self, h, control):
        # Bin 1x1 only, no GPS, no full-well curve.
        return QHY_SUCCESS if control == QHY_CONTROL.CAM_BIN1X1MODE else 1

    def _GetQHYCCDParam(self, h, control):
        return {QHY_CONTROL.CURTEMP: -10.0, QHY_CONTROL.CURPWM: 51.0}.get(control, 0.0)

    def _GetQHYCCDCurrentROI(self, h, sx, sy, nx, ny):
        for ref, v in ((sx, 0), (sy, 0), (nx, W), (ny, H)):
            self._set(ref, v)
        return QHY_SUCCESS

    def _GetQHYCCDMemLength(self, h):
        return W * H * 2

    def _GetQHYCCDPreciseExposureInfo(self, *args):
        return 1  # unsupported, as on the QHY174GPS

    def _GetQHYCCDRollingShutterEndOffset(self, *args):
        return 1

    def _GetQHYCCDSingleFrame(self, h, w, hh, bpp, ch, data):
        for ref, v in ((w, W), (hh, H), (bpp, 16), (ch, 1)):
            self._set(ref, v)
        for i in range(W * H):
            data[i] = (i % 4096) << 4
        return QHY_SUCCESS


@pytest.fixture
def cam(monkeypatch):
    fake = FakeLib()
    monkeypatch.setattr(camera_device, "load_qhyccd_library", lambda path: fake)
    dev = CameraDevice(
        DeviceConfig(entity="C8-test", sensor_bpp=12, sdk_timeout=1.0), "/nonexistent.so"
    )
    dev.connected = True
    assert dev.connected
    dev.fake = fake
    return dev


def wait_ready(dev, timeout=5.0):
    t0 = time.monotonic()
    while not dev.image_ready:
        assert time.monotonic() - t0 < timeout, f"no image, state={dev.camera_state!r}"
        time.sleep(0.01)


def test_every_sdk_call_runs_on_the_worker_thread(cam):
    cam.start_exposure(0.05, True)
    wait_ready(cam)
    img = cam.image_array
    assert img.shape == (W, H)
    assert img[1, 0] == 1  # 12-bit data right-shifted back to ADU
    threads = {t for name, t in cam.fake.calls if name != "CancelQHYCCDExposingAndReadout"}
    assert threads == {"sdk-C8-test"}, threads
    assert not cam.fake.overlap


def test_exposure_and_readout_share_one_thread(cam):
    cam.start_exposure(0.05, True)
    wait_ready(cam)
    exp = [t for n, t in cam.fake.calls if n == "ExpQHYCCDSingleFrame"]
    get = [t for n, t in cam.fake.calls if n == "GetQHYCCDSingleFrame"]
    assert exp == get == ["sdk-C8-test"]


def test_image_array_does_not_touch_the_sdk(cam):
    cam.start_exposure(0.05, True)
    wait_ready(cam)
    before = len(cam.fake.calls)
    cam.image_array
    cam.image_array
    assert len(cam.fake.calls) == before


def test_status_reads_mid_exposure_come_from_cache(cam):
    cam.fake.block["GetQHYCCDSingleFrame"] = gate = threading.Event()
    cam.start_exposure(0.05, True)
    time.sleep(0.2)
    assert cam.exposure_in_progress
    before = len(cam.fake.calls)
    assert cam.ccd_temperature == -10.0
    assert cam.cooler_power == pytest.approx(51.0 / 255.0 * 100.0)
    assert len(cam.fake.calls) == before
    gate.set()
    wait_ready(cam)


def test_settings_changes_refused_mid_exposure(cam):
    cam.fake.block["GetQHYCCDSingleFrame"] = gate = threading.Event()
    cam.start_exposure(0.05, True)
    time.sleep(0.2)
    with pytest.raises(RuntimeError, match="during an exposure"):
        cam.gain = 5
    with pytest.raises(RuntimeError, match="during an exposure"):
        cam.set_ccd_temperature = -5.0
    gate.set()
    wait_ready(cam)
    cam.gain = 5  # fine again once idle


def test_hung_readout_wedges_instead_of_blocking(cam):
    cam.fake.block["GetQHYCCDSingleFrame"] = threading.Event()  # never set
    cam.start_exposure(0.05, True)
    t0 = time.monotonic()
    while cam.camera_state != CameraState.ERROR:
        assert time.monotonic() - t0 < 5.0, cam.camera_state
        time.sleep(0.02)
    assert cam.sdk_wedged and "GetQHYCCDSingleFrame" in cam.sdk_wedged
    assert not cam.image_ready
    # Later calls fail at once instead of queueing behind the stuck one.
    t0 = time.monotonic()
    with pytest.raises(SdkWedgedError):
        cam.start_exposure(0.05, True)
    with pytest.raises(SdkWedgedError):
        _ = cam.gain
    assert time.monotonic() - t0 < 0.5


def test_abort_mid_exposure_cancels_and_discards(cam):
    cam.start_exposure(2.0, True)
    time.sleep(0.2)
    cam.abort_exposure()
    assert cam.camera_state == CameraState.IDLE
    names = [n for n, _ in cam.fake.calls]
    assert "CancelQHYCCDExposingAndReadout" in names
    time.sleep(0.3)
    assert "GetQHYCCDSingleFrame" not in names
    assert not cam.image_ready
    cam.start_exposure(0.05, True)  # the camera is usable again
    wait_ready(cam)


def test_abort_during_readout_does_not_cancel_the_transfer(cam):
    cam.fake.block["GetQHYCCDSingleFrame"] = gate = threading.Event()
    cam.start_exposure(0.05, True)
    time.sleep(0.2)
    assert cam.camera_state == CameraState.DOWNLOADING
    cam.abort_exposure()
    assert "CancelQHYCCDExposingAndReadout" not in [n for n, _ in cam.fake.calls]
    gate.set()
    time.sleep(0.2)
    assert not cam.image_ready  # the late frame is discarded


def test_concurrent_start_exposure_only_one_wins(cam):
    cam.fake.block["GetQHYCCDSingleFrame"] = gate = threading.Event()
    errors = []

    def start():
        try:
            cam.start_exposure(0.05, True)
        except RuntimeError as e:
            errors.append(e)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors) == 7
    gate.set()
    wait_ready(cam)
    assert [n for n, _ in cam.fake.calls].count("ExpQHYCCDSingleFrame") == 1


def test_disconnect_waits_for_a_live_readout(cam):
    cam.fake.block["GetQHYCCDSingleFrame"] = gate = threading.Event()
    cam.start_exposure(0.05, True)
    time.sleep(0.2)
    threading.Timer(0.3, gate.set).start()
    cam.disconnect()
    names = [n for n, _ in cam.fake.calls]
    assert names.index("CloseQHYCCD") > names.index("GetQHYCCDSingleFrame")
    assert not cam.fake.overlap
    assert not cam.connected


def test_http_stays_responsive_while_readout_hangs(cam):
    """The bug on jetson008: a hung download froze every endpoint for 8 s+."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import camera

    app = FastAPI()
    app.include_router(camera.router)
    camera.set_devices({0: cam})
    cam.fake.block["GetQHYCCDSingleFrame"] = gate = threading.Event()
    with TestClient(app) as client:
        r = client.put("/api/v1/camera/0/startexposure", data={"Duration": "0.05", "Light": "true"})
        assert r.json()["ErrorNumber"] == 0, r.json()
        time.sleep(0.2)
        t0 = time.monotonic()
        for prop in ("connected", "camerastate", "ccdtemperature", "imageready"):
            r = client.get(f"/api/v1/camera/0/{prop}")
            assert r.json()["ErrorNumber"] == 0, (prop, r.json())
        assert time.monotonic() - t0 < 0.5
        assert client.get("/api/v1/camera/0/imageready").json()["Value"] is False
        gate.set()
        wait_ready(cam)
        r = client.get("/api/v1/camera/0/imagearray")
        assert r.json()["ErrorNumber"] == 0
        assert len(r.json()["Value"]) == W


def test_filter_wheel_uses_the_camera_worker(cam):
    from cfw_device import CFWDevice
    from config import CFWConfig

    cam.fake._GetQHYCCDCFWStatus = lambda h, buf: (buf.__setitem__(0, b"\x03"), QHY_SUCCESS)[1]
    cfw = CFWDevice(CFWConfig(entity="C8-wheel", names=list("BGRLuiz"), timeout=2.0), cam)
    cfw.connected = True
    cfw.position = 3
    t0 = time.monotonic()
    while cfw.position != 3:
        assert time.monotonic() - t0 < 3.0
        time.sleep(0.05)
    cfw_threads = {t for n, t in cam.fake.calls if "CFW" in n}
    assert cfw_threads == {"sdk-C8-test"}, cfw_threads

    cam.fake.block["GetQHYCCDSingleFrame"] = gate = threading.Event()
    cam.start_exposure(0.05, True)
    time.sleep(0.2)
    with pytest.raises(RuntimeError, match="during an exposure"):
        cfw.position = 1
    gate.set()
    wait_ready(cam)


def test_gps_timing_survives_the_readout_move(cam):
    """QHY174GPS latches ~20 us apart, LOCKED: expect GPS-DERIVED timing."""
    import ctypes

    from libqhyccd import QHY_GPS

    end_s = 1_000_000_000  # GPS seconds of the frame-end latch
    fake = cam.fake
    plain = fake._GetQHYCCDSingleFrame

    def frame_with_gps(h, w, hh, bpp, ch, data):
        res = plain(h, w, hh, bpp, ch, data)
        g = QHY_GPS.from_address(ctypes.addressof(data))
        g.StartFlag = g.EndFlag = g.NowFlag = 0x30  # LOCKED
        g.StartSeconds, g.EndSeconds, g.NowSeconds = end_s, end_s, end_s
        g.StartCounts[:] = [0, 0, 0]
        g.EndCounts[:] = [0, 0, 200]  # 20 us after start
        g.NowCounts[:] = [0, 0, 200]
        return res

    fake._GetQHYCCDSingleFrame = frame_with_gps
    cam._has_gps = True
    cam.start_exposure(0.05, True)
    wait_ready(cam)
    t = cam._timing
    assert t["TIME-SRC"] == "GPS-DERIVED", t
    assert t["GPS-STAT"] == "LOCKED"
    assert t["DATE-END"] == QHY_GPS.create_timestamp(end_s, bytes([0, 0, 200]))
    from astropy.time import Time

    span = (Time(t["DATE-END"]) - Time(t["DATE-OBS"])).to_value("sec")
    assert span == pytest.approx(0.05, abs=1e-6)
    assert cam.last_exposure_start_time == t["DATE-OBS"]
