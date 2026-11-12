"""power.* and system.devices actions of dahlia-agent (fake sysfs, fake lspci/lsusb)."""

import pytest
from dahlia_agent import actions, power, protocol
from test_agent import FakeRunner, err_code, run


@pytest.fixture
def ctx(tmp_path):
    return actions.Context(runner=FakeRunner(), proc=tmp_path / "proc", sys=tmp_path / "sys",
                           etc=tmp_path / "etc")


def make_cpufreq(ctx, n=2, governor="powersave",
                 avail="performance powersave", driver="intel_pstate"):
    for i in range(n):
        p = ctx.sys / f"devices/system/cpu/cpufreq/policy{i}"
        p.mkdir(parents=True)
        (p / "scaling_available_governors").write_text(avail + "\n")
        (p / "scaling_governor").write_text(governor + "\n")
        (p / "scaling_driver").write_text(driver + "\n")
        (p / "scaling_cur_freq").write_text(f"{(i + 1) * 1200000}\n")


def test_get_without_cpufreq(ctx):
    out = run(ctx, "power.get")["result"]
    assert out == {"available": [], "current": None, "driver": None, "saved": None,
                   "frequencies": []}


def test_get_status(ctx):
    make_cpufreq(ctx, avail="performance schedutil powersave")
    out = run(ctx, "power.get")["result"]
    assert out["available"] == ["powersave", "schedutil", "performance"]
    assert out["current"] == "powersave"
    assert out["driver"] == "intel_pstate"
    assert out["frequencies"] == [1200, 2400]
    assert out["saved"] is None


def test_get_mixed_governors(ctx):
    make_cpufreq(ctx)
    (ctx.sys / "devices/system/cpu/cpufreq/policy1/scaling_governor").write_text("performance")
    assert run(ctx, "power.get")["result"]["current"] == "performance,powersave"


def test_set_governor_writes_sysfs_and_tmpfiles(ctx):
    make_cpufreq(ctx)
    out = run(ctx, "power.set_governor", {"governor": "performance"})["result"]
    assert out["current"] == "performance"
    assert out["saved"] == "performance"
    conf = (ctx.etc / "tmpfiles.d/dahlia-cpu-governor.conf").read_text()
    assert ("w /sys/devices/system/cpu/cpufreq/policy*/scaling_governor - - - - performance\n"
            in conf)
    for i in range(2):
        gov = ctx.sys / f"devices/system/cpu/cpufreq/policy{i}/scaling_governor"
        assert gov.read_text() == "performance"


def test_set_governor_rejects_unknown(ctx):
    make_cpufreq(ctx)
    assert err_code(run(ctx, "power.set_governor", {"governor": "ondemand"})) \
        == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "power.set_governor", {"governor": "x; rm -rf /"})) \
        == protocol.INVALID_PARAMS
    assert not (ctx.etc / "tmpfiles.d/dahlia-cpu-governor.conf").exists()


def test_set_governor_without_cpufreq(ctx):
    assert err_code(run(ctx, "power.set_governor", {"governor": "powersave"})) == protocol.FAILED


LSPCI = """Slot:\t00:02.0
Class:\tVGA compatible controller [0300]
Vendor:\tIntel Corporation [8086]
Device:\tAlder Lake-N [UHD Graphics] [46d1]
SVendor:\tIntel Corporation [8086]
Rev:\t00
Driver:\ti915
Module:\ti915
IOMMUGroup:\t0

Slot:\t01:00.0
Class:\tEthernet controller [0200]
Vendor:\tRealtek Semiconductor Co., Ltd. [10ec]
Device:\tRTL8125 2.5GbE Controller [8125]
IOMMUGroup:\t7

"""

LSUSB = """Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 003: ID 0764:0601 Cyber Power System, Inc. PR1500LCDRT2U UPS
garbage line
"""


def test_parse_lspci():
    gpu, nic = power.parse_lspci(LSPCI)
    assert gpu == {"slot": "00:02.0", "class": "VGA compatible controller", "class_id": "0300",
                   "vendor": "Intel Corporation", "device": "Alder Lake-N [UHD Graphics]",
                   "id": "8086:46d1", "driver": "i915", "iommu_group": 0}
    assert nic["driver"] is None
    assert nic["iommu_group"] == 7
    assert nic["id"] == "10ec:8125"


def test_parse_lsusb():
    hub, ups = power.parse_lsusb(LSUSB)
    assert hub["hub"] is True
    assert ups == {"bus": 1, "device": 3, "id": "0764:0601",
                   "name": "Cyber Power System, Inc. PR1500LCDRT2U UPS", "hub": False}


def test_devices_action(ctx):
    ctx.runner.answers[("lspci",)] = (0, LSPCI, "")
    ctx.runner.answers[("lsusb",)] = (0, LSUSB, "")
    (ctx.sys / "kernel/iommu_groups/0").mkdir(parents=True)
    out = run(ctx, "system.devices")["result"]
    assert len(out["pci"]) == 2
    assert len(out["usb"]) == 2
    assert out["iommu"] is True
    assert ["lspci", "-vmm", "-nn", "-k"] in ctx.runner.calls


def test_devices_without_tools(ctx):
    ctx.runner.answers[("lspci",)] = (127, "", "not found")
    ctx.runner.answers[("lsusb",)] = (127, "", "not found")
    assert run(ctx, "system.devices")["result"] == {"pci": [], "usb": [], "iommu": False}
