"""
Filter wheel device driver using the QHYCCD SDK CFW functions.

Controls a filter wheel connected to the camera via the 4-pin CFW cable.
Requires a connected CameraDevice instance (shares the same SDK handle).
"""

import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from ctypes import c_char_p, c_uint32, create_string_buffer, POINTER

from config import CFWConfig
from log import get_logger


logger = get_logger()

QHY_SUCCESS = 0


class CFWBusyError(RuntimeError):
    """Raised when a command is rejected because the wheel is currently moving."""


class CFWDevice:
    """Filter wheel controlled via the QHYCCD SDK (4-pin CFW cable to camera)."""

    def __init__(self, cfw_config: CFWConfig, camera_device):
        """
        Args:
            cfw_config: Filter wheel configuration (names, offsets, etc.)
            camera_device: A CameraDevice instance whose SDK handle we share.
        """
        self._config = cfw_config
        self._camera = camera_device

        self._connected = False
        self._connecting = False
        self._moving = False
        self._position: int = -1

    @property
    def _handle(self):
        """Borrow the camera's SDK handle."""
        return self._camera.handle

    @property
    def _lib(self):
        """Borrow the camera's loaded library."""
        return self._camera.libqhyccd

    # ─── ASCOM Common ────────────────────────────────────────────────

    def connect(self) -> None:
        """Connect to the filter wheel (requires camera already connected)."""
        if self._connected or self._connecting:
            return

        self._connecting = True
        try:
            if not self._camera.connected:
                raise RuntimeError(
                    "Camera must be connected before the filter wheel"
                )

            # Declare CFW function signatures if not already done
            self._ensure_cfw_signatures()

            # Check if CFW is plugged in
            res = self._lib.IsQHYCCDCFWPlugged(self._handle)
            if res != QHY_SUCCESS:
                raise RuntimeError(
                    "No filter wheel detected (IsQHYCCDCFWPlugged failed)"
                )

            # Read initial position
            self._position = self._read_position()
            self._connected = True
            logger.info(
                f"Connected to filter wheel: {self._config.entity} "
                f"(position {self._position})"
            )
        except Exception as e:
            logger.error(f"CFW connect failed: {e}")
            self._connected = False
            raise
        finally:
            self._connecting = False

    def disconnect(self) -> None:
        """Disconnect from the filter wheel."""
        self._connected = False
        self._moving = False
        logger.info(f"Disconnected from filter wheel: {self._config.entity}")

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        if value and not self._connected:
            self.connect()
        elif not value and self._connected:
            self.disconnect()

    @property
    def connecting(self) -> bool:
        return self._connecting

    @property
    def entity(self) -> str:
        return self._config.entity

    # ─── IFilterWheel ────────────────────────────────────────────────

    @property
    def focus_offsets(self) -> List[int]:
        return self._config.focus_offsets

    @property
    def names(self) -> List[str]:
        return self._config.names

    @property
    def position(self) -> int:
        """Return current filter position (0-based), or -1 if moving."""
        if self._moving:
            return -1
        return self._position

    @position.setter
    def position(self, value: int) -> None:
        """Command the wheel to move to a position (0-based)."""
        if not self._connected:
            raise RuntimeError("Filter wheel is not connected")
        if self._moving:
            raise CFWBusyError("Filter wheel is currently moving")

        num_filters = len(self._config.names)
        if value < 0 or value >= num_filters:
            raise ValueError(
                f"Position {value} out of range (0-{num_filters - 1})"
            )

        # SDK uses ASCII position string
        order = str(value).encode()
        res = self._lib.SendOrder2QHYCCDCFW(self._handle, order, c_uint32(len(order)))
        if res != QHY_SUCCESS:
            raise RuntimeError(f"SendOrder2QHYCCDCFW failed (res={res})")

        self._moving = True
        logger.info(f"Moving filter wheel to position {value}")
        threading.Thread(
            target=self._wait_for_move, args=(value,), daemon=True
        ).start()

    @property
    def timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # ─── Internal ────────────────────────────────────────────────────

    def _ensure_cfw_signatures(self) -> None:
        """Declare ctypes signatures for CFW functions (idempotent)."""
        lib = self._lib
        if hasattr(lib, "_cfw_sigs_set"):
            return

        lib.IsQHYCCDCFWPlugged.argtypes = [POINTER(c_uint32)]
        lib.IsQHYCCDCFWPlugged.restype = c_uint32

        lib.GetQHYCCDCFWStatus.argtypes = [POINTER(c_uint32), c_char_p]
        lib.GetQHYCCDCFWStatus.restype = c_uint32

        lib.SendOrder2QHYCCDCFW.argtypes = [POINTER(c_uint32), c_char_p, c_uint32]
        lib.SendOrder2QHYCCDCFW.restype = c_uint32

        lib._cfw_sigs_set = True
        logger.debug("CFW ctypes signatures registered")

    def _read_position(self) -> int:
        """Query current filter wheel position from SDK.

        GetQHYCCDCFWStatus writes a short string into the buffer.
        Typical response is a single ASCII digit or a multi-char status.
        """
        buf = create_string_buffer(64)
        res = self._lib.GetQHYCCDCFWStatus(self._handle, buf)
        if res != QHY_SUCCESS:
            logger.warning(f"GetQHYCCDCFWStatus failed (res={res})")
            return -1

        status = buf.value.decode().strip()
        logger.debug(f"CFW status: {status!r}")

        # The SDK typically returns the position as a single character
        # or a string like "N" where N is the 0-based position digit.
        # It may also return special values during movement.
        if status and status[0].isdigit():
            return int(status[0])

        # If we can't parse a position, assume moving
        return -1

    def _wait_for_move(self, target: int) -> None:
        """Poll CFW status until the target position is reached or timeout."""
        timeout = self._config.timeout
        t0 = time.time()

        # Give the wheel a moment to start moving
        time.sleep(1)

        while self._moving:
            pos = self._read_position()
            if pos == target:
                self._position = target
                self._moving = False
                logger.info(f"Filter wheel arrived at position {target}")
                return

            if (time.time() - t0) > timeout:
                self._moving = False
                logger.error(
                    f"Filter wheel move to {target} timed out after {timeout}s"
                )
                # Update position to whatever we last read
                if pos >= 0:
                    self._position = pos
                return

            time.sleep(1)
