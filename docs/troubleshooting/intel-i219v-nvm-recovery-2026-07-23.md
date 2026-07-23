# Intel I219-V NVM recovery log — 2026-07-23

## Scope and safety boundary

- Host: `neovara`
- Goal: recover the onboard Ethernet interface for later Proxmox use; Proxmox is not being installed in this session.
- Protected storage: do not modify, format, repartition, mount, or otherwise access `/dev/sdb`, `/dev/sdb1`, or `/dev/sdb2`.
- Kubernetes: do not modify the cluster or start `k3s-agent`.
- NVM: do not run an EEPROM/NVM write utility or flash an image without a verified backup, exact adapter/OEM image, recovery path, brick-risk explanation, and explicit approval.

The protected block devices were not queried or accessed. No Kubernetes command was run. `k3s-agent` was checked read-only at the beginning and end and remained `inactive` and `disabled`.

## Executive conclusion

The onboard controller enumerates reliably on PCI as Intel `8086:15b8`, is in PCI D0, supports an isolated Advanced Features Function-Level Reset (`af_flr`), and shows no PCI parity/abort error. The installed `e1000e` module recognizes this PCI ID, but probe stops before netdev registration because NVM checksum validation fails three times. An isolated PCI function reset followed by a clean e1000e unload/reload did not change the failure.

This is not presently fixable with a supported e1000e module parameter. Upstream already resets the controller and retries checksum validation three times to cover transient ASPM cases. A UEFI factory reset, Onboard LAN disable cycle, five-minute full shutdown/power-state reset, and reboot were subsequently completed by the user. The controller re-enumerated, but the identical checksum failure remained. Retained UEFI configuration and ordinary auxiliary-power state are therefore ruled out.

No BIOS, firmware, EEPROM, or NVM image was flashed. No generic I219-V image is acceptable: this is a Thirdwave Diginnos OEM system with custom BIOS `P1.41Z`, while the base board identifies as ASRock Z370M Pro4. Intel directs users of onboard Ethernet to the system/board manufacturer for the proper package. Neither fwupd nor the local system exposes an exact Thirdwave update or recovery image.

## Hardware, OS, and firmware identity

Commands:

```text
date -Is
uname -a
cat /etc/os-release
hostnamectl
cat /sys/class/dmi/id/{board_vendor,board_name,bios_vendor,bios_version,bios_date}
lspci -nnk
sudo dmidecode -t 0 -t 1 -t 2 -t 11
```

Relevant output:

```text
2026-07-23T03:45:54+00:00
Linux neovara 6.8.0-136-generic #136-Ubuntu SMP PREEMPT_DYNAMIC Wed Jul 1 21:53:05 UTC 2026 x86_64
Ubuntu 24.04.4 LTS

System manufacturer: Thirdwave Diginnos Co., Ltd.
System product:      Diginnos PC
Base board vendor:   ASRock
Base board product:  Z370M Pro4
BIOS vendor:         American Megatrends Inc.
BIOS version:        P1.41Z
BIOS date:           12/08/2017

00:1f.6 Ethernet controller [0200]:
  Intel Corporation Ethernet Connection (2) I219-V [8086:15b8]
  Subsystem: Intel Corporation Ethernet Connection (2) I219-V [8086:15b8]
  Kernel modules: e1000e
```

The ASRock product page confirms that the retail Z370M Pro4 uses an Intel I219-V onboard PHY. The `P1.41Z` suffix and Thirdwave system identity indicate an OEM-custom firmware lineage, so a retail ASRock image must not be assumed compatible.

## PCI and power state

Commands:

```text
sudo lspci -vv -s 00:1f.6
cat /sys/bus/pci/devices/0000:00:1f.6/{vendor,device,subsystem_vendor,subsystem_device,class,enable,power_state}
readlink /sys/bus/pci/devices/0000:00:1f.6/driver
ls -l /sys/bus/pci/devices/0000:00:1f.6/reset*
cat /sys/bus/pci/devices/0000:00:1f.6/reset_method
```

Relevant output:

```text
Control: I/O- Mem+ BusMaster- ... ParErr- SERR-
Status:  Cap+ ... ParErr- >TAbort- <TAbort- <MAbort- >SERR- <PERR-
Region 0: Memory at df100000 [size=128K]
Power Management Status: D0
Advanced Features: FLR+

vendor=0x8086
device=0x15b8
subsystem_vendor=0x8086
subsystem_device=0x15b8
class=0x020000
enable=0
power_state=D0
driver: unbound
reset_method: af_flr
```

Interpretation:

- The PCI function is present and responsive, so UEFI has not hidden/disabled it at enumeration time.
- D0 and clean PCI status make a PCI addressing or runtime-suspend failure unlikely.
- `enable=0` here is the Linux PCI enable reference count after failed probe, not evidence that the UEFI Onboard LAN setting is off.
- There was no netdev for the onboard controller.

## Network and service state

Commands:

```text
ip -br link
ip -br address
ip route
systemctl is-active k3s-agent
systemctl is-enabled k3s-agent
```

Relevant output:

```text
wlxbc071de42a95 UP 192.168.1.111/24
default via 192.168.1.1 dev wlxbc071de42a95 metric 600

k3s-agent: inactive
k3s-agent: disabled
```

Wi-Fi is the management/default path. There was no physical Ethernet netdev such as `enp0s31f6`.

## Kernel and driver evidence

Commands:

```text
lsmod | rg '^e1000e\b'
modinfo e1000e
for f in /sys/module/e1000e/parameters/*; do printf '%s=' "${f##*/}"; cat "$f"; done
journalctl -k -b --no-pager | rg -i -C4 'e1000e|00:1f\.6|nvm|checksum'
journalctl --list-boots --no-pager
```

Relevant output:

```text
e1000e 356352 0
filename: /lib/modules/6.8.0-136-generic/kernel/drivers/net/ethernet/intel/e1000e/e1000e.ko.zst
alias: pci:v00008086d000015B8sv*sd*bc*sc*i*
copybreak=256

e1000e 0000:00:1f.6: Interrupt Throttling Rate (ints/sec) set to dynamic conservative mode
e1000e 0000:00:1f.6: The NVM Checksum Is Not Valid
e1000e: probe of 0000:00:1f.6 failed with error -5
```

The same failure occurred at boot on `2026-07-23 02:45:07`, `03:11:39`, and `03:32:21`, and after the controlled retry at `03:49:33`. The installed module has no checksum-bypass parameter. `copybreak` is unrelated to probe/NVM validation.

Upstream Linux v6.8 behavior for this exact device family:

- PCI ID `0x15B8` maps to `E1000_DEV_ID_PCH_SPT_I219_V2` and the `board_pch_spt` implementation.
- Probe resets the controller before reading NVM.
- Probe retries checksum validation three times because some ASPM systems can fail the first attempt.
- If all three validations fail, probe emits this exact message and returns `-EIO`.
- The generic validator adds NVM words `0x00` through `0x3f` and requires the 16-bit sum to equal `0xBABA`.

Therefore, adding a guessed `pcie_aspm=off`, using a different interrupt parameter, or repeatedly reloading the same driver is not justified by the evidence. A one-off ASPM read race has already been retried internally and across multiple boots.

### Important e1000e conditional-write caveat

For the `pch_spt` path, upstream `e1000_validate_nvm_checksum_ich8lan()` can conditionally set the `NVM_COMPAT_VALID_CSUM` bit and recompute/commit the checksum if the OEM-valid bit is clear. This is part of ordinary e1000e probe, not a separate repair command. It ran during every boot before this session.

The single manual module reload below repeated ordinary probe. No NVM utility or explicit NVM write was run, and the non-debug kernel log cannot prove whether the conditional branch attempted a write. A successful conditional repair should have allowed generic validation to pass; it did not. To respect the approval boundary, do not run more probe cycles or any NVM tooling until a verified read-only backup method and explicit approval exist.

## BIOS/UEFI assessment

Read-only findings:

```text
Firmware supports UEFI, but the installed OS was booted in legacy BIOS mode.
Current OEM BIOS: P1.41Z dated 2017-12-08.
fwupdmgr: No updatable devices.
fwupdmgr warning: UEFI firmware cannot be updated in legacy BIOS mode.
```

The ASRock manual documents:

- `Advanced > Chipset Configuration > Onboard LAN`: enables/disables the onboard NIC.
- `Advanced > Chipset Configuration > Deep Sleep`: controls shutdown power saving.
- `Advanced > ACPI Configuration > PCIE Devices Power On`: controls PCIe/Wake-on-LAN wake.
- `Exit > Load UEFI Defaults`: resets all settings.

Do **not** load all UEFI defaults as an initial recovery step; that could also change boot/CSM and storage settings. The narrow physical test is to record current settings, toggle only Onboard LAN, and remove auxiliary power as described below.

## Firmware/vendor guidance and decision

Read-only command:

```text
fwupdmgr get-devices --no-unreported-check
sudo fwupdmgr get-updates --no-unreported-check
```

Result:

```text
No updatable devices
```

Decision:

- Intel's support guidance says drivers/firmware for an Ethernet connection built into a non-Intel board must come from the board/system manufacturer.
- The ASRock retail page confirms the hardware family but does not establish that its retail BIOS is compatible with Thirdwave's custom `P1.41Z`.
- Thirdwave warns that an incorrect or failed OEM BIOS update can leave a Diginnos PC unbootable and tells users to identify the exact product before updating.
- No exact Thirdwave system model, matching OEM BIOS package, I219 NVM image, or vendor recovery procedure has been confirmed.

No firmware update or NVM image is approved at this checkpoint.

## Non-destructive recovery attempt performed

Upstream Linux documents the per-device `reset` sysfs file: if present, writing `1` performs an isolated function reset. This device exposed `reset_method=af_flr`, was unbound, and had no netdev or users.

Commands executed at `2026-07-23T03:49:33+00:00`:

```text
echo 1 > /sys/bus/pci/devices/0000:00:1f.6/reset
cat /sys/bus/pci/devices/0000:00:1f.6/power_state
modprobe -r e1000e
modprobe e1000e
sleep 2
lspci -nnk -s 00:1f.6
ip -br link
journalctl -k --since '-2 minutes' --no-pager |
  grep -Ei 'e1000e|00:1f.6|NVM Checksum'
```

Result:

```text
reset_rc=0
power_state=D0
unload_rc=0
load_rc=0

e1000e 0000:00:1f.6: The NVM Checksum Is Not Valid
e1000e: probe of 0000:00:1f.6 failed with error -5
```

No Ethernet netdev appeared. The attempt did not fix the fault.

Rollback:

- PCI FLR is transient and has no persistent configuration to undo.
- The module was left loaded, matching normal boot state; it remains unbound from the failed device.
- No boot parameter, modprobe configuration, network configuration, BIOS setting, firmware, or NVM image was changed.

## Physical UEFI/power checkpoint — completed, unsuccessful

The requested physical procedure was:

1. Record the existing UEFI boot/CSM, SATA mode, Onboard LAN, Deep Sleep, and PCIe wake settings. Do not load defaults.
2. In `Advanced > Chipset Configuration`, set only `Onboard LAN` to **Disabled**, save, and shut down fully.
3. Switch the PSU off and unplug AC power. Disconnect the Ethernet cable. Press the case power button for 15–30 seconds, then leave AC disconnected for at least five minutes. This removes standby/auxiliary power that an in-OS FLR cannot remove.
4. Reconnect AC, enter UEFI, set `Onboard LAN` to **Enabled**, save, and boot Ubuntu.
5. Do not start K3s. Validate with:

   ```text
   lspci -nnk -s 00:1f.6
   ip -br link
   journalctl -k -b --no-pager | grep -Ei 'e1000e|00:1f.6|NVM Checksum'
   ```

Success requires an e1000e-bound PCI device and a stable physical netdev. Link, DHCP/static addressing, packet transfer, and another reboot must then be tested before calling the NIC suitable for Proxmox.

Rollback for the UEFI toggle is to restore the recorded Onboard LAN setting. Do not alter SATA mode, CSM/boot mode, or boot order.

The user reported completing a UEFI factory reset, disabling Onboard LAN, shutting the workstation down for five minutes, and rebooting. Read-only validation after the boot at `2026-07-23 04:23:43 UTC` showed:

```text
00:1f.6 Ethernet controller [0200]:
  Intel Corporation Ethernet Connection (2) I219-V [8086:15b8]
  Kernel modules: e1000e

e1000e 0000:00:1f.6: The NVM Checksum Is Not Valid
e1000e: probe of 0000:00:1f.6 failed with error -5
```

There was still no onboard Ethernet netdev, and Wi-Fi remained the default route. The controller's presence shows that Onboard LAN was enabled by the time Linux booted. The BIOS remained Thirdwave `P1.41Z`; the OS continued to boot in legacy BIOS mode. `k3s-agent` remained `inactive` and `disabled`.

Conclusion: the factory reset, LAN toggle, shutdown, and cold-power interval did not repair the NVM checksum. Do not repeat the cycle or run further manual e1000e probes.

## Chassis-label identity and vendor package search

The rear chassis label was photographed and its manufacturing number was used only with Thirdwave's official configuration lookup. The number itself is intentionally omitted from this log because it is a service identifier.

Official lookup result:

```text
Model: Magnate ZS 8400 (EM01/Z370)
Original CPU: Intel Core i5-8400
Original motherboard: ASRock Z370M Pro4 (Z370, LGA1151, DDR4, micro-ATX)
Original GPU: Palit GeForce GTX 1050 2 GB
```

The model and original motherboard match the live DMI/PCI investigation. The currently installed GTX 1660 SUPER differs from the shipped GPU, but that does not change the identity of the onboard I219-V or its board-integrated NVM.

Thirdwave's public download page was checked after the model was identified. It links to the manufacturing-number configuration lookup and general manuals, but no public model-specific BIOS, Intel GbE-region/NVM image, or supported NVM recovery utility was found for `Magnate ZS 8400 (EM01/Z370)` or Thirdwave BIOS `P1.41Z`.

Conclusion: exact system identity is now confirmed, but the image/tool/recovery requirements for an NVM write are not. Do not use a retail ASRock BIOS, a generic Intel package, or an image from another Z370M Pro4. The next safe escalation is a Thirdwave support request containing the private manufacturing number and asking for:

1. The OEM BIOS/GbE firmware package that exactly matches Magnate ZS 8400 (EM01/Z370), ASRock Z370M Pro4, and the `P1.41Z` firmware lineage.
2. Confirmation that the package covers onboard Intel I219-V PCI ID `8086:15b8`.
3. A vendor-supported method to read and back up the existing GbE/NVM region before any write, including how to verify the backup.
4. A documented restore or board-recovery procedure if the update fails or the system stops booting.
5. Confirmation whether Thirdwave will perform the repair if no safe customer procedure exists.
6. Explicit confirmation about whether a retail ASRock BIOS is unsupported for this OEM system.

No support request was submitted and no firmware was downloaded or executed in this session.

Suggested vendor request (insert the private manufacturing number only in Thirdwave's form):

```text
Subject: Magnate ZS 8400 onboard Intel I219-V NVM checksum failure

System: Magnate ZS 8400 (EM01/Z370)
Motherboard: ASRock Z370M Pro4
Current OEM BIOS: P1.41Z
Onboard NIC: Intel Ethernet Connection (2) I219-V, PCI 8086:15b8
Linux error: "e1000e: The NVM Checksum Is Not Valid"; probe fails with error -5.

Loading BIOS defaults, toggling onboard LAN, removing AC/standby power for five
minutes, PCI function reset, and one driver reload did not change the error.

Please provide or identify the exact OEM-supported BIOS/GbE recovery package for
this manufacturing number. Before any write, I also need a supported method to
back up and verify the current GbE/NVM region and a documented recovery procedure
if the write fails. Please confirm whether retail ASRock firmware is unsupported
for this OEM system. If no customer-safe procedure exists, can Thirdwave perform
the board/NVM repair?
```

## Read-only Intel SPI/GbE-region backup

Because vendor contact was excluded, a read-only backup path through the Intel SPI controller was investigated. This did not access any protected data disk or Kubernetes resource.

The Ubuntu `flashrom` 1.3 package was downloaded and unpacked under `/tmp`; it was not installed system-wide. Upstream flashrom documents probe-only operation followed by `-r` as the board-read test and backup path. Probe-only detection reported:

```text
Found chipset "Intel Z370" [8086:a2c9].
SPI Configuration is locked down.
FREG0: Flash Descriptor region 0x00000000-0x00000fff is read-only.
FREG1: BIOS region 0x00200000-0x00ffffff is read-write.
FREG2: Management Engine region 0x00003000-0x001fffff is read-only.
FREG3: Gigabit Ethernet region 0x00001000-0x00002fff is read-write.
Found "Opaque flash chip" (16384 kB).
```

Both Ubuntu 1.3 and the downloaded, SHA-256-verified flashrom 1.7.0 source mark Z370 as **not tested**. Therefore flashrom is not treated as an approved writer on this board. The tool prints `Enabling flash write...` while initializing the Intel internal programmer, even for probe/read operations; this changes chipset access-control state transiently but is not a flash-data write. No `-w`, erase, force, board-enable, or write-protection command was run.

Only descriptor region `gbe` was read, twice:

```text
flashrom -p internal -c 'Opaque flash chip' \
  -r partial1.rom --ifd -i gbe:gbe1.bin
flashrom -p internal -c 'Opaque flash chip' \
  -r partial2.rom --ifd -i gbe:gbe2.bin

read1_rc=0
read2_rc=0
size=8192 bytes each
SHA-256=749e8b54d31bf6f0dde823d325b2172fdf95c74b9e486b65b8f8ba229feaeb30
byte comparison=identical
```

After a diagnostic driver could register the netdev, a second read path was used:

```text
ethtool -e enp0s31f6 raw on
size=4096 bytes
SHA-256=ade55b678e36708d91e9161feaeef48dcf14b47bc19a4301953743a7dbd0f47c
comparison=identical to the first 4096-byte active bank in the flashrom dump
```

Durable local copies, exact modified driver source, and hashes are stored privately at:

```text
/home/neovara/nic-recovery-backups/2026-07-23-i219v/
```

The directory is mode `0700`; backup files are mode `0600`. `SHA256SUMS` verified successfully after copying and syncing. These copies are on the workstation's system disk, not `/dev/sdb`. They are durable local backups but are **not off-host backups**, so the off-host requirement for an NVM write remains unsatisfied.

## Exact checksum analysis

Linux v6.8 e1000e reads the valid SPT bank and adds words `0x00` through `0x3f`; the 16-bit result must equal `0xBABA`. The two 4 KiB banks were analyzed without printing their MAC or raw contents:

```text
bank 0: signature valid,   compat-valid bit 1
        sum 0x22F7, stored checksum 0xDAFF, calculated checksum 0x72C2
bank 1: signature invalid, compat-valid bit 1
        sum 0x13BA, stored checksum 0xDAFF, calculated checksum 0x81FF
```

The banks differ in only three bytes, all in the first 128 bytes. Bank 0 is the active bank selected by the upstream signature rule, but its checksum is definitively invalid. Because its `NVM_COMPAT_VALID_CSUM` bit is already 1, the upstream conditional OEM repair branch was not entered during the earlier driver probes; those probes did not issue that branch's NVM writes.

The identical invalid checksum word in both nearly identical banks suggests inconsistent duplicated NVM content rather than an intermittent read or power-state problem. The invalid `PBA No: FFFFFF-0FF` later reported by the bypassed driver is further evidence that the image should not be trusted merely because its MAC address is valid.

## Unsupported non-writing driver workaround

There is no upstream checksum-bypass module parameter. To test whether the hardware remains usable without changing flash, the exact Ubuntu source package `linux-source-6.8.0=6.8.0-136.136` was downloaded; its package SHA-256 matched Ubuntu metadata:

```text
10336d32348cb1e59fe1c722f2e0b3861e80b015ee01cf339a528c3eb0e4e6ee
```

A local `allow_bad_nvm` boolean module parameter was added. When enabled, it skips the validation call entirely and prints an unsupported-warning message. It does not call the upstream validation routine, checksum updater, or any NVM write operation. Default behavior remains unchanged unless the parameter is explicitly enabled.

The module built successfully against the exact running `6.8.0-136-generic` headers. Its vermagic matches the stock module and its SHA-256 is:

```text
2e3b9d49bedf53e1b205848be936b23f5b7cfd9abdbee5d8332286468ee740f4
```

Transient load at `2026-07-23 04:49 UTC` succeeded:

```text
e1000e 0000:00:1f.6: UNSUPPORTED: skipping NVM checksum validation; NVM is not being written
interface=enp0s31f6
driver=e1000e
link=1000 Mb/s full duplex
DHCP=successful
selected RX/TX/CRC/ECC error counters=0
```

NetworkManager automatically created and activated `Wired connection 1`; this was not manually configured. Four initial IPv4 gateway pings succeeded with zero loss. A later burst of gateway IPv4 pings was unanswered, but the link stayed up, another LAN peer answered 4/4 over IPv4, and the router answered 4/4 over IPv6 link-local at sub-millisecond latency. This points to router ICMP behavior rather than loss of carrier or a driver reset, but longer post-reboot testing is still required.

For the current Ubuntu kernel only, the workaround was installed alongside the untouched stock module:

```text
/lib/modules/6.8.0-136-generic/updates/nic-recovery/e1000e.ko
/etc/modprobe.d/e1000e-nvm-workaround.conf
/etc/initramfs-tools/hooks/e1000e-nvm-workaround
```

`depmod` and `update-initramfs -u -k 6.8.0-136-generic` completed successfully. The generated initramfs contains the custom module, the opt-in configuration, and the stock module. Its `modules.dep` selects `updates/nic-recovery/e1000e.ko`, and the custom copy in the initramfs has the expected SHA-256 above.

Rollback for this software workaround:

```text
sudo modprobe -r e1000e
sudo rm /etc/modprobe.d/e1000e-nvm-workaround.conf
sudo rm /etc/initramfs-tools/hooks/e1000e-nvm-workaround
sudo rm /lib/modules/6.8.0-136-generic/updates/nic-recovery/e1000e.ko
sudo depmod 6.8.0-136-generic
sudo update-initramfs -u -k 6.8.0-136-generic
sudo modprobe e1000e
```

The final `modprobe` intentionally returns to the stock checksum failure. The automatically created NetworkManager profile can be left in place; it is inert when no Ethernet netdev exists.

This is an unsupported workaround, not an NVM repair. It is tied to Ubuntu kernel `6.8.0-136-generic`, taints the running kernel as out-of-tree, and is not guaranteed to build or operate on a future Proxmox kernel. No Proxmox software was installed.

## Reboot validation — passed for current Ubuntu kernel

The workstation rebooted at `2026-07-23 04:59:50 UTC`. More than 18 minutes after boot, validation showed:

```text
kernel=6.8.0-136-generic
loaded e1000e srcversion=9A2CBE708614C2ACD7B920B
resolved module=/lib/modules/6.8.0-136-generic/updates/nic-recovery/e1000e.ko
PCI 0000:00:1f.6 driver=e1000e
interface=enp0s31f6
speed=1000 Mb/s
duplex=full
link=yes
NetworkManager=connected
k3s-agent=inactive, disabled
```

The boot journal contained the explicit bypass warning, PHC registration, and 1 Gb/s link-up message. It contained no NVM-checksum failure, probe failure, driver reset, NETDEV watchdog, AER, or PCIe bus error.

Forced-interface traffic tests:

```text
IPv4 LAN peer:       100/100 replies, 0% loss
IPv6 router:           20/20 replies, 0% loss
HTTPS over Ethernet:   HTTP 200, 5,298,400 bytes transferred
RX/TX/CRC/ECC errors:  0 before and after
link after tests:      1000 Mb/s full duplex, up
```

Wi-Fi remained the preferred default route (`metric 600` versus Ethernet `metric 20100`), so existing access was not displaced. Ethernet traffic was explicitly bound to `enp0s31f6` for validation. Proxmox networking would need to select and bridge this physical interface deliberately; the current NetworkManager metric is not a Proxmox configuration.

Conclusion: the unsupported, non-writing e1000e bypass survives reboot and provides a stable physical Ethernet interface on the current Ubuntu kernel. It satisfies the immediate workstation workaround, but it does not repair NVM and does not establish compatibility with a different Proxmox kernel.

## Proxmox VE 9.2 installer media — built and validated

The inserted installer was identified before any write:

```text
device=/dev/sdc
removable=yes
model=SanDisk Ultra
serial=[redacted in public log]
capacity=30,752,636,928 bytes
original label=PVE
Proxmox VE=9.2, ISO release 1
installer kernel=7.0.2-6-pve
```

The protected Samsung disk remained separate:

```text
/dev/sdb  Samsung HD155UI  serial [redacted in public log]
/dev/sdb1 mounted at /mnt/storage1
/dev/sdb2 mounted at /mnt/longhorn-immich
```

Neither `/dev/sdb` partition was unmounted or written during the media work.

The ISO kernel package's `SOURCE` file identifies exact Proxmox kernel commit
`269ae89a92e50fef6802944d03d404ff28cd38f9`. Its Ubuntu kernel submodule is
commit `69bb061d6b71ee9b43e6584cc16d2a8853e81fe6`. Exact e1000e files were
retrieved from that Ubuntu commit. The signed Proxmox repository's matching
header package was verified before extraction:

```text
package=proxmox-headers-7.0.2-6-pve_7.0.2-6_amd64.deb
SHA-256=fee00d4604ceb2e32b5174536309734ff7040c74dc967b085e135dedcc1a1aa2
compiler=gcc 14.2.0
```

The same non-writing `allow_bad_nvm` change was ported to this exact source and
built against the exact PVE headers. Validation showed:

```text
vermagic=7.0.2-6-pve SMP preempt mod_unload modversions
PCI alias includes=8086:15b8
parameter=allow_bad_nvm:bool
custom module SHA-256=5d11f5e1599fa4a92fe695a6ff33f05209cfc9683d4d499bdde70f352ffbfdf7
```

The installer needed three integration points:

1. Early initrd, because `/init` scans and loads PCI network drivers before
   mounting the installer filesystems.
2. Live installer squashfs, for the switched-root installer environment.
3. The offline `7.0.2-6-pve` kernel package, so the installed system gets the
   workaround on first boot.

All three use `updates/nic-recovery/e1000e.ko` and:

```text
options e1000e allow_bad_nvm=1
```

`depmod` metadata resolves e1000e to the `updates/` module in every environment.
The installed package retains Proxmox's original signed module at its stock
path, adds exact patched source and rollback notes under
`/usr/local/share/i219v-nvm-workaround/`, and runs `depmod` even while the
installer's `/proxmox_install_mode` marker exists.

Embedded artifact hashes:

```text
early initrd:
  3eb154b0d8ec4529ac78a4588b1f38049820d6d5468920800ec009246be4eb23
live installer squashfs:
  aad604e0780eed6c7ee7c7ad5e0b3f181304f5a8c8d6e3d6ba62f9118e8184d2
installed kernel package:
  f90e5e83eb7c257f130c52903cd04a409df0fe52576c848a0ed392861ae9ad6f
```

The squashfs was rebuilt with its original zstd level 19, 1 MiB block size,
eight UID/GID identities, device node, and xattrs. The Debian package indexes,
compressed index, and Release hashes were regenerated and verified against the
repacked kernel package.

Before overwriting the USB, an exact local backup of the original 1,706,178,560
byte hybrid image was read from `/dev/sdc`:

```text
/tmp/proxmox-ve_9.2-1-original-usb-image.iso
SHA-256=4e88fe416df9b527624a175f24c9aa07c714d3332afb1ee3dbf3879573ef2c6c
```

This rollback image is on the Ubuntu system disk and will not survive if that
disk is overwritten by Proxmox.

The rebuilt image preserves BIOS GRUB, UEFI, protective MBR, GPT, APM, HFS+,
and the `PVE` volume identity:

```text
/tmp/proxmox-ve_9.2-1-i219v-recovery.iso
size=1,709,592,576 bytes
SHA-256=65e5ea078e019ba5017acce6b66f78ea0dfec0672f39eca94772e628f5de8f25
```

`xorriso -check_media` read every one of its 834,762 sectors with `+ good`.
A no-disk QEMU/SeaBIOS smoke test booted the hybrid image, passed through the
modified initrd and squashfs, and reached the Proxmox VE 9.2 graphical
installer's expected `No Hard Disk found!` page. No installation was started.

Immediately before the destructive write, `/dev/sdc` serial and capacity were
checked again and both `/dev/sdb` mounts were rechecked. The custom image was
then written only to `/dev/sdc`. An independent read of the first 834,762
sectors from the physical USB produced the exact image hash:

```text
65e5ea078e019ba5017acce6b66f78ea0dfec0672f39eca94772e628f5de8f25
```

The USB was mounted read-only after write. Its initrd, live squashfs, installed
kernel package, custom module copies, modprobe option, and package index all
matched the expected hashes. `/dev/sdb1` and `/dev/sdb2` remained mounted.

The GPT backup header intentionally ends at the hybrid ISO image boundary, not
the end of the 28.6 GiB USB. Partition tools can therefore offer to expand or
repair the GPT. **Do not accept that offer**; the unused trailing USB capacity
is normal for an ISO-hybrid image.

The custom module is unsigned. This workstation currently boots in legacy BIOS
mode; use that existing boot mode for this installer. Secure Boot module
enforcement would reject the local module.

The workaround in the installed package is ABI-specific to `7.0.2-6-pve`.
Before booting a newer PVE kernel, rebuild the module against that kernel's
matching headers. For rollback on the installed `7.0.2-6-pve` system:

```text
rm /lib/modules/7.0.2-6-pve/updates/nic-recovery/e1000e.ko
rm /etc/modprobe.d/e1000e-nvm-workaround.conf
depmod 7.0.2-6-pve
update-initramfs -u -k 7.0.2-6-pve
```

This returns to the original signed driver and therefore to the original NVM
checksum probe failure. No Proxmox installation and no NIC NVM write occurred
during media preparation.

## NVM-repair gate

No EEPROM/NVM write may proceed unless all of the following are satisfied:

1. Exact Thirdwave system model and service identity are confirmed. **Satisfied:** Magnate ZS 8400 (EM01/Z370).
2. The adapter is confirmed as the onboard `8086:15b8` I219-V2 for this specific board/OEM build. **Satisfied.**
3. A vendor-provided image/update package explicitly matches the Thirdwave model and current firmware lineage. **Not satisfied.**
4. A supported read-only tool can dump the existing GbE/NVM region without changing it. **Partially satisfied:** two reads and an independent ethtool active-bank read agree, but flashrom still marks Z370 untested.
5. At least two dumps are byte-identical and their hashes are stored off-host. **Partially satisfied:** byte-identical, hashed durable local copies exist; no off-host copy exists.
6. The restore/recovery method is documented and available even if the NIC or board stops booting. **Not satisfied:** internal SPI access is possible while Linux boots, but no external programmer/recovery path is prepared and Z370 writing is untested.
7. The brick risk is explained and the user gives explicit approval. **Not satisfied; no write approval has been requested or given.**

A generic I219-V dump, a dump from another Z370M Pro4, a retail ASRock BIOS, or a checksum-only write is not sufficient.

Initial discovery found no netdev-based backup path. The later Intel SPI read produced two matching full GbE-region copies, and the bypassed driver produced a matching active-bank read. This resolves the read/identity uncertainty but does not make a flash write safe: current flashrom still marks Z370 untested, no off-host copy exists, and no external recovery programmer is prepared.

## Official/upstream references

- [ASRock Z370M Pro4 product page](https://www.asrock.com/mb/Intel/Z370M%20Pro4/index.asp) — identifies the onboard Intel I219-V.
- [ASRock Z370M Pro4 manual](https://download.asrock.com/Manual/Z370M%20Pro4.pdf) — documents Onboard LAN, Deep Sleep, PCIe wake, and UEFI settings.
- [Linux v6.8 e1000e probe source](https://github.com/torvalds/linux/blob/v6.8/drivers/net/ethernet/intel/e1000e/netdev.c) — reset, three checksum retries, and `-EIO` failure path.
- [Linux v6.8 ICH/PCH NVM source](https://github.com/torvalds/linux/blob/v6.8/drivers/net/ethernet/intel/e1000e/ich8lan.c) — I219/PCH checksum validation and conditional OEM-valid-bit handling.
- [Linux v6.8 generic NVM source](https://github.com/torvalds/linux/blob/v6.8/drivers/net/ethernet/intel/e1000e/nvm.c) — checksum calculation and expected sum.
- [Linux PCI sysfs ABI](https://www.kernel.org/doc/html/latest/admin-guide/abi-testing.html) — isolated device reset and PCI bind/rescan semantics.
- [Linux e1000e driver documentation](https://docs.kernel.org/networking/device_drivers/ethernet/intel/e1000e.html) — supported driver/module parameters.
- [Intel: drivers for built-in network adapters](https://www.intel.com/content/www/us/en/support/articles/000035361/ethernet-products.html) — use the non-Intel board manufacturer's download.
- [Intel I219-V support/download page](https://www.intel.com/content/www/us/en/products/sku/82186/intel-ethernet-connection-i219v/downloads.html) — product identification and driver packages; it does not provide a board-specific NVM image.
- [Thirdwave/Dospara firmware update warning](https://faq3.dospara.co.jp/faq/show/8308?category_id=251) — exact product identification and BIOS-update risk.
- [Thirdwave configuration lookup](https://cts.dospara.co.jp/5support/info.php?ope=prime_conf) — identifies the shipped model and component configuration from the chassis manufacturing number.
- [Thirdwave download page](https://www.dospara.co.jp/support/spr_download.html) — public configuration/manual download route; no exact BIOS or I219-V NVM recovery package was published there for this model.
- [Thirdwave support contact page](https://www.dospara.co.jp/support/spr_inquiry.html) — requires the chassis manufacturing number for product-specific support.
- [flashrom manual](https://flashrom.org/classic_cli_manpage.html) — documents probe-only operation, `-r` backup, Intel layouts, and write/recovery cautions.
- [flashrom Intel ME/descriptor guidance](https://flashrom.org/user_docs/management_engine.html) — explains Intel flash regions and host access restrictions.
- [flashrom release archive](https://download.flashrom.org/releases/) — official 1.7.0 source, signature, and checksum files used to inspect current Z370 support status.
- [Proxmox pve-kernel source mirror](https://github.com/proxmox/pve-kernel) — official read-only mirror of the package source identified by the ISO's `SOURCE` file.
- [Proxmox VE package repository](http://download.proxmox.com/debian/pve/) — signed repository used to verify the exact `7.0.2-6-pve` header package.
- [Ubuntu resolute kernel source](https://git.launchpad.net/~ubuntu-kernel/ubuntu/+source/linux/+git/resolute/) — exact e1000e source files at the Proxmox kernel package's pinned submodule commit.
