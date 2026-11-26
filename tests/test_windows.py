"""The Windows-only parts that can be checked on any computer: reading USB
device ids, choosing the installer as the update, and the installer's icon."""

import importlib.util
import struct
from pathlib import Path

from luxtunes.core import updater, windows_devices


def test_apple_devices_and_the_ipods_firewire_id_from_usb_ids():
    ids = ["USB\\VID_05AC&PID_1261\\000A27001387CE50",      # an iPod classic
           "USB\\VID_046D&PID_C52B\\5&2A1B3C&0&1",            # a mouse
           "USB\\VID_05AC&PID_1267\\8&ABC&0&2"]                # an interface, not the device
    assert windows_devices.apple_devices(ids) == [("1261", "000A27001387CE50")]


def test_the_windows_copy_updates_with_the_installer():
    release = updater.Release("1.0.0", "v1.0.0", "", "", [
        updater.Asset("LuxTunes-x86_64.AppImage", "u1"),
        updater.Asset("luxtunes-1.0.0-py3-none-any.whl", "u2"),
        updater.Asset("LuxTunes-1.0.0-Setup.exe", "u3")])
    assert release.asset("windows").name == "LuxTunes-1.0.0-Setup.exe"
    assert release.asset("appimage").name.endswith(".AppImage")
    assert release.asset("installed").name.endswith(".whl")


def test_the_installers_icon(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "windows_build", Path(__file__).parents[1] / "packaging" / "windows" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    png = tmp_path / "icon.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(100))
    ico = tmp_path / "icon.ico"
    build.make_icon(png, ico)
    data = ico.read_bytes()
    assert struct.unpack_from("<HHH", data) == (0, 1, 1)
    assert struct.unpack_from("<I", data, 6 + 8)[0] == len(png.read_bytes())
    assert data[22:] == png.read_bytes()
    assert build.version()
