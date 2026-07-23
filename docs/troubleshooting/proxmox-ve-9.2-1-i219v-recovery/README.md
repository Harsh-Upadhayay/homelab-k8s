# Proxmox VE 9.2-1 Intel I219-V recovery installer

This directory documents the unofficial
`proxmox-ve_9.2-1-i219v-recovery.iso` release artifact. It is a narrowly
modified Proxmox VE 9.2-1 installer for the workstation recorded in the
[full recovery log](../intel-i219v-nvm-recovery-2026-07-23.md):

```text
controller: Intel Ethernet Connection (2) I219-V
PCI ID:     8086:15b8
failure:    e1000e rejects the device after three failed NVM checksum checks
kernel:     7.0.2-6-pve
```

The image lets the installer and first installed boot use the onboard
Ethernet interface even though its NVM checksum is invalid. It does **not**
repair, rewrite, or otherwise modify the NIC NVM.

Download the ISO and checksum manifest from the
[GitHub Release](https://github.com/Harsh-Upadhayay/homelab-k8s/releases/tag/proxmox-ve-9.2-1-i219v-recovery-v1).

## Release files

| File | Purpose |
|---|---|
| `proxmox-ve_9.2-1-i219v-recovery.iso` | Bootable hybrid BIOS/UEFI installer; distributed as a GitHub Release asset, not stored in Git |
| `SHA256SUMS` | Checksum manifest for the release ISO |
| [`BUILD-MANIFEST.md`](./BUILD-MANIFEST.md) | Exact input commits, package and embedded-artifact hashes, build layout, and validation evidence |
| [`e1000e-allow-bad-nvm.patch`](./e1000e-allow-bad-nvm.patch) | Complete source change applied to the exact kernel source |
| [`RELEASE_NOTES.md`](./RELEASE_NOTES.md) | GitHub Release notes |

Verify the downloaded ISO before writing it:

```bash
sha256sum -c SHA256SUMS
```

Expected ISO:

```text
size:    1,709,592,576 bytes
SHA-256: 65e5ea078e019ba5017acce6b66f78ea0dfec0672f39eca94772e628f5de8f25
```

## What is modified

The exact e1000e source selected by the Proxmox kernel package gains one
opt-in Boolean parameter:

```text
allow_bad_nvm=1
```

With the option enabled, `e1000e_probe()` emits this warning and skips its
checksum validator:

```text
NVM checksum validation bypassed by allow_bad_nvm=1
```

The rest of probe proceeds normally. The added path contains no EEPROM or NVM
write call. The image enables the option at three places so Ethernet remains
available across the complete installation lifecycle:

1. the boot media's early initrd;
2. the live installer squashfs; and
3. a repacked offline `proxmox-kernel-7.0.2-6-pve-signed` package installed
   onto the target system.

Each environment places the custom module at:

```text
/lib/modules/7.0.2-6-pve/updates/nic-recovery/e1000e.ko
```

and configures:

```text
/etc/modprobe.d/e1000e-nvm-workaround.conf
options e1000e allow_bad_nvm=1
```

`depmod` metadata gives the module in `updates/` precedence. The original
signed Proxmox e1000e module remains untouched at its stock path. The installed
kernel package also carries the patched driver source and rollback notes under:

```text
/usr/local/share/i219v-nvm-workaround/
```

## What is not modified

- No NIC NVM, EEPROM, BIOS, or firmware is written.
- No generic or donor I219-V NVM image is included.
- The stock signed e1000e module is not removed.
- The installer workflow and target-disk selection are not automated.
- The image does not change Proxmox networking after installation; the
  physical interface still needs to be selected for the intended bridge.
- The image does not make the workaround survive a later PVE kernel ABI
  change.

## Applicability and risks

This image was built and tested for the recorded `8086:15b8` workstation. The
module parameter bypasses validation globally for e1000e devices loaded by
this image, so this is not a general-purpose Proxmox distribution.

An invalid checksum can represent corrupted or incorrect hardware-specific
configuration. Allowing probe to continue may produce wrong MAC/configuration
data, unreliable networking, or hardware-specific failures. The actual
workstation passed sustained link and traffic tests with no RX/TX/CRC/ECC
errors, but that result is not a guarantee for another machine.

The custom module is unsigned. Use the workstation's existing legacy BIOS boot
mode, or otherwise ensure Secure Boot module enforcement is disabled. Secure
Boot can reject the custom module even if the installer itself starts.

The image is unofficial and unsupported by Proxmox, Intel, Ubuntu, and the
system manufacturer. Proxmox VE and bundled software retain their original
licenses and trademarks.

## Write and boot

Writing an ISO destroys the selected device. Resolve the USB path from stable
identity immediately before writing it; do not reuse `/dev/sdX` from an older
session.

```bash
lsblk -o NAME,PATH,RM,TRAN,SIZE,MODEL,SERIAL,MOUNTPOINTS
sha256sum -c SHA256SUMS
sudo umount /dev/disk/by-id/usb-<installer-device>-part* 2>/dev/null || true
sudo dd if=proxmox-ve_9.2-1-i219v-recovery.iso \
  of=/dev/disk/by-id/usb-<installer-device> \
  bs=4M conv=fsync status=progress
sync
```

Do not substitute a whole-disk path until its model, serial, capacity, and
removable flag identify the intended USB. Never target a mounted data disk.

The ISO is a hybrid image. Its backup GPT ends at the ISO image boundary, not
at the end of a larger USB drive. If a partition tool offers to repair or
expand that GPT, decline; unused trailing USB capacity is expected.

Boot the installer in legacy BIOS mode on the tested workstation. Proxmox's
normal installer still controls target-disk selection and will overwrite the
selected operating-system disk. The workaround does not protect disks from an
incorrect installer selection.

## First-boot verification

Before using Ethernet as the management path, verify the installed system:

```bash
uname -r
modinfo -n e1000e
cat /sys/module/e1000e/parameters/allow_bad_nvm
journalctl -b -k | grep -E 'e1000e|NVM checksum'
ip -br link
ethtool <physical-interface>
```

Expected essentials:

```text
kernel:       7.0.2-6-pve
module path:  /lib/modules/7.0.2-6-pve/updates/nic-recovery/e1000e.ko
parameter:    Y
journal:      NVM checksum validation bypassed by allow_bad_nvm=1
link:         detected and stable at the negotiated speed/duplex
```

Confirm management connectivity and error counters before relying on the
interface:

```bash
ip -s link show <physical-interface>
ethtool -S <physical-interface>
ping -I <physical-interface> -c 20 <lan-peer>
```

## Kernel upgrades

The module is ABI-specific to `7.0.2-6-pve`. Do not make a newer PVE kernel the
default until an equivalent module has been rebuilt against that kernel's
matching headers and tested. Keep the working kernel installed and available
in the boot menu as the recovery path.

Merely copying this `e1000e.ko` into a newer kernel tree is unsafe and will
normally fail module version checks.

## Rollback

On an installed `7.0.2-6-pve` system:

```bash
rm /lib/modules/7.0.2-6-pve/updates/nic-recovery/e1000e.ko
rm /etc/modprobe.d/e1000e-nvm-workaround.conf
depmod 7.0.2-6-pve
update-initramfs -u -k 7.0.2-6-pve
reboot
```

This restores resolution to the original signed driver. On the affected
workstation, that driver will again reject the controller because the checksum
failure itself remains unrepaired.

## Validation performed

- Confirmed the custom module's `7.0.2-6-pve` vermagic and `8086:15b8` alias.
- Verified module precedence and the parameter in the initrd, live squashfs,
  and repacked installed kernel package.
- Regenerated and verified the offline Debian package indexes and Release
  hashes.
- Read every ISO sector with `xorriso -check_media`: all 834,762 sectors good.
- Booted the complete ISO through initrd and live squashfs in QEMU/SeaBIOS to
  the expected graphical installer `No Hard Disk found!` screen; no
  installation was started.
- Wrote the physical SanDisk installer USB only after rechecking identity and
  protected mounts.
- Read the written ISO-length region back from the USB and reproduced the
  release ISO SHA-256 exactly.

The chronological commands, observations, workstation tests, and NVM-repair
gate are preserved in the
[full troubleshooting log](../intel-i219v-nvm-recovery-2026-07-23.md).
