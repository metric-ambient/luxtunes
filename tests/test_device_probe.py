"""The background device check (core/device_probe.py): what it reads, and
that it only reads what it needs to."""

from luxtunes.core import cd, device_probe
from luxtunes.core.android import device as android_device
from luxtunes.core.ipod import device as ipod_device


def test_a_new_disc_is_read_once(monkeypatch):
    reads = []
    monkeypatch.setattr(cd, "drives", lambda: ["/dev/sr0", "/dev/sr1"])
    monkeypatch.setattr(cd, "audio_disc_present", lambda d: d == "/dev/sr0")
    monkeypatch.setattr(cd, "read_toc", lambda d: reads.append(d) or "toc")
    found = device_probe.cds(set())
    assert found["/dev/sr0"].present and found["/dev/sr0"].toc == "toc"
    assert not found["/dev/sr1"].present and found["/dev/sr1"].toc is None
    device_probe.cds({"cd:/dev/sr0"})                # already known: not read again
    assert reads == ["/dev/sr0"]


def test_a_disc_that_cant_be_read_is_reported(monkeypatch):
    monkeypatch.setattr(cd, "drives", lambda: ["/dev/sr0"])
    monkeypatch.setattr(cd, "audio_disc_present", lambda d: True)

    def broken(device):
        raise cd.CdError("scratched")
    monkeypatch.setattr(cd, "read_toc", broken)
    drive = device_probe.cds(set())["/dev/sr0"]
    assert isinstance(drive.error, cd.CdError) and drive.toc is None


def test_ipods_are_found_and_read_once(tmp_path, monkeypatch):
    ipod = tmp_path / "IPOD"
    (ipod / "iPod_Control" / "iTunes").mkdir(parents=True)
    other = tmp_path / "STICK"
    other.mkdir()
    mounts = [("/dev/sdb1", str(ipod), "vfat"), ("/dev/sdc1", str(other), "vfat"),
              ("proc", "/proc", "proc")]
    monkeypatch.setattr(ipod_device, "mounted_filesystems", lambda: mounts)
    read = []
    monkeypatch.setattr(ipod_device, "read_ipod", lambda m, d, f: read.append(m) or f"iPod at {m}")
    mounted, ipod_mounts, new = device_probe.ipods(set(), set())
    assert set(mounted) == {str(ipod), str(other)} and ipod_mounts == {str(ipod)}
    assert new == {str(ipod): f"iPod at {ipod}"}
    device_probe.ipods({str(ipod)}, {str(ipod)})      # already known: not read again
    assert read == [str(ipod)]


def test_phones_are_only_listed_when_something_changed(monkeypatch):
    listed = []
    monkeypatch.setattr(android_device, "unmounted_phones", lambda: [])
    monkeypatch.setattr(android_device, "mounted_names", lambda: {"mtp:host=Pixel"})
    monkeypatch.setattr(android_device, "find_phones", lambda: listed.append(1) or ["phone"])
    assert device_probe.phones(set(), False)[2] == ["phone"]
    assert device_probe.phones({"mtp:host=Pixel"}, False)[2] is None     # the same as before
    assert device_probe.phones({"mtp:host=Pixel"}, True)[2] == ["phone"]  # one still waiting
    assert len(listed) == 2


def test_no_apple_device_no_lsblk(monkeypatch):
    monkeypatch.setattr(ipod_device, "apple_usb_connected", lambda: False)
    monkeypatch.setattr(ipod_device, "unmounted_ipod_partitions",
                        lambda: (_ for _ in ()).throw(AssertionError("lsblk ran")))
    assert device_probe.ipod_candidates() == (False, [])
