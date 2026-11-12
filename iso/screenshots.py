"""Screenshots of the installer as a user sees it, for checking the artwork.

    python3 iso/screenshots.py <bios|uefi> <path-to-iso>

Boots the default entry ("Install Dahlia Linux") in QEMU with a graphics card,
answers the first questions by key presses and saves PNGs of the screen in
build/screens-<firmware>/: the boot menu, d-i's account and password
questions and the first Dahlia questions. Besides the named steps it saves a
frame every 15 seconds (t-<seconds>.png), so a slow step still shows up.
Timing problems never fail the script; only a QEMU that does not start does.

Needs qemu-system-x86_64, xorriso, /dev/kvm; for uefi also OVMF.
"""

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVMF_CODE = "/usr/share/OVMF/OVMF_CODE_4M.fd"
OVMF_VARS = "/usr/share/OVMF/OVMF_VARS_4M.fd"
USER = "fabi"
PASSWORD = "Dahlia-Screens-1"  # noqa: S105 (throwaway VM)


class Qmp:
    def __init__(self, path: Path):
        for _ in range(100):
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(str(path))
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("QEMU monitor did not come up")
        self.file = self.sock.makefile("rw")
        self.lock = threading.Lock()
        self.file.readline()  # greeting
        self.cmd("qmp_capabilities")

    def cmd(self, name: str, **args) -> dict:
        with self.lock:
            self.file.write(json.dumps({"execute": name, "arguments": args}) + "\n")
            self.file.flush()
            while True:
                reply = json.loads(self.file.readline())
                if "return" in reply or "error" in reply:
                    return reply

    def shot(self, path: Path) -> None:
        self.cmd("screendump", filename=str(path), format="png")

    def keys(self, *names: str) -> None:
        for name in names:
            self.cmd("send-key", keys=[{"type": "qcode", "data": name}])
            time.sleep(0.15)

    def type(self, text: str) -> None:
        for ch in text:
            if ch.isupper():
                self.cmd("send-key", keys=[{"type": "qcode", "data": "shift"},
                                           {"type": "qcode", "data": ch.lower()}])
                time.sleep(0.15)
            else:
                self.keys({"-": "minus"}.get(ch, ch))
        self.keys("ret")


def remaster(iso: str, out: Path) -> Path:
    """The release ISO, with menus that wait and the keyboard answered on the
    kernel line (it is asked before anything else could answer it)."""
    grub = (ROOT / "iso/grub.cfg").read_text().replace("set timeout=5", "set timeout=-1")
    grub = grub.replace("priority=high ---", "priority=high keymap=us ---", 1)
    isolinux = (ROOT / "iso/isolinux.cfg").read_text().replace("timeout 50", "timeout 0")
    isolinux = isolinux.replace("priority=high ---", "priority=high keymap=us ---", 1)
    (out / "grub.cfg").write_text(grub)
    (out / "isolinux.cfg").write_text(isolinux)
    target = out / "screens.iso"
    subprocess.run(["xorriso", "-indev", iso, "-outdev", str(target),
                    "-map", str(out / "grub.cfg"), "/boot/grub/grub.cfg",
                    "-map", str(out / "isolinux.cfg"), "/isolinux/isolinux.cfg",
                    "-boot_image", "any", "replay"],
                   check=True, capture_output=True)
    return target


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("bios", "uefi"):
        raise SystemExit(__doc__)
    mode, iso = sys.argv[1], sys.argv[2]
    out = ROOT / "build" / f"screens-{mode}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    test_iso = remaster(iso, out)
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(out / "disk.qcow2"), "20G"],
                   check=True)
    qmp_path = out / "qmp.sock"
    args = ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-m", "2048", "-smp", "2",
            "-display", "none", "-vga", "std", "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-drive", f"file={out / 'disk.qcow2'},if=virtio,format=qcow2",
            "-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0",
            "-cdrom", str(test_iso), "-boot", "d"]
    if mode == "uefi":
        shutil.copy(OVMF_VARS, out / "OVMF_VARS.fd")
        args += ["-machine", "q35",
                 "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
                 "-drive", f"if=pflash,format=raw,file={out / 'OVMF_VARS.fd'}"]
    qemu = subprocess.Popen(args)
    try:
        qmp = Qmp(qmp_path)
        start = time.monotonic()
        stop = threading.Event()

        def frames() -> None:
            while not stop.wait(15):
                qmp.shot(out / f"t-{int(time.monotonic() - start):03d}.png")

        threading.Thread(target=frames, daemon=True).start()
        steps = [
            (15, "01-boot-menu", ["ret"]),
            (100, "02-account", [USER]),
            (6, "03-password", [PASSWORD]),
            (4, "04-password-again", [PASSWORD]),
            (60, "05-disk", ["ret"]),
            (6, "06-hostname", ["ret"]),
            (6, "07-timezone", []),
        ]
        for wait, name, answers in steps:
            time.sleep(wait)
            qmp.shot(out / f"{name}.png")
            for answer in answers:
                if answer == "ret":
                    qmp.keys("ret")
                else:
                    qmp.type(answer)
        stop.set()
    finally:
        qemu.kill()
        qemu.wait()
    (out / "disk.qcow2").unlink(missing_ok=True)
    test_iso.unlink(missing_ok=True)
    print("\n".join(sorted(p.name for p in out.glob("*.png"))))


if __name__ == "__main__":
    main()
