# Build manifest

## Release identity

```text
release tag:   proxmox-ve-9.2-1-i219v-recovery-v1
release asset: proxmox-ve_9.2-1-i219v-recovery.iso
size:          1,709,592,576 bytes
SHA-256:       65e5ea078e019ba5017acce6b66f78ea0dfec0672f39eca94772e628f5de8f25
```

The unmodified installer image read from the already-flashed USB before
repacking was:

```text
release:  Proxmox VE 9.2, ISO release 1
size:     1,706,178,560 bytes
SHA-256:  4e88fe416df9b527624a175f24c9aa07c714d3332afb1ee3dbf3879573ef2c6c
```

That original image is not redistributed by this release.

## Kernel and source pins

```text
installer kernel ABI:
  7.0.2-6-pve

ISO kernel package SOURCE commit:
  269ae89a92e50fef6802944d03d404ff28cd38f9

Ubuntu kernel source submodule commit:
  69bb061d6b71ee9b43e6584cc16d2a8853e81fe6

unmodified netdev.c SHA-256:
  0b8a68f9490ab8c927f80fccfb2cb64485408fb38d58b42db5768ae1cec3f54c

matching header package:
  proxmox-headers-7.0.2-6-pve_7.0.2-6_amd64.deb

header package SHA-256:
  fee00d4604ceb2e32b5174536309734ff7040c74dc967b085e135dedcc1a1aa2
```

Source origins:

- Proxmox kernel packaging: <https://github.com/proxmox/pve-kernel>
- Ubuntu kernel source: <https://git.launchpad.net/~ubuntu-kernel/ubuntu/+source/linux/+git/resolute/>
- Signed Proxmox package repository: <http://download.proxmox.com/debian/pve/>

The complete source change is
[`e1000e-allow-bad-nvm.patch`](./e1000e-allow-bad-nvm.patch). The repacked
installed kernel package also contains the exact patched e1000e driver source
under `/usr/local/share/i219v-nvm-workaround/source/`.

## Build outputs

```text
installed custom e1000e.ko:
  5d11f5e1599fa4a92fe695a6ff33f05209cfc9683d4d499bdde70f352ffbfdf7

early initrd:
  3eb154b0d8ec4529ac78a4588b1f38049820d6d5468920800ec009246be4eb23

live installer squashfs:
  aad604e0780eed6c7ee7c7ad5e0b3f181304f5a8c8d6e3d6ba62f9118e8184d2

modified installed kernel package:
  f90e5e83eb7c257f130c52903cd04a409df0fe52576c848a0ed392861ae9ad6f

final ISO:
  65e5ea078e019ba5017acce6b66f78ea0dfec0672f39eca94772e628f5de8f25
```

The installed custom module reports:

```text
license:   GPL v2
name:      e1000e
vermagic:  7.0.2-6-pve SMP preempt mod_unload modversions
parameter: allow_bad_nvm:bool
PCI alias: includes pci:v00008086d000015B8...
```

## Integration layout

The custom module and option are integrated into all three environments:

```text
early initrd:
  lib/modules/7.0.2-6-pve/updates/nic-recovery/e1000e.ko
  etc/modprobe.d/e1000e-nvm-workaround.conf

live installer:
  usr/lib/modules/7.0.2-6-pve/updates/nic-recovery/e1000e.ko
  etc/modprobe.d/e1000e-nvm-workaround.conf

installed kernel package:
  lib/modules/7.0.2-6-pve/updates/nic-recovery/e1000e.ko
  etc/modprobe.d/e1000e-nvm-workaround.conf
  usr/local/share/i219v-nvm-workaround/README
  usr/local/share/i219v-nvm-workaround/source/
```

The original signed module remains at:

```text
lib/modules/7.0.2-6-pve/kernel/drivers/net/ethernet/intel/e1000e/e1000e.ko
SHA-256: 8cad504342df1798e9e22636b0129674c4f2d0309c5e6eced2f129259296587d
```

The rebuilt squashfs preserves its original:

```text
compression: zstd level 19
block size:  1 MiB
identity:    eight original UID/GID entries
metadata:    device nodes and xattrs
```

The hybrid ISO preserves its BIOS GRUB, UEFI, protective MBR, GPT, APM, HFS+,
and `PVE` volume identity.

## Toolchain recorded on the build host

```text
gcc:          14.2.0
xorriso:      1.5.6
mksquashfs:   4.6.1
dpkg-deb:     1.22.6
QEMU:         8.2.2
```

## Validation record

```text
xorriso media check:
  834,762 sectors read
  result: + good

QEMU smoke test:
  firmware: SeaBIOS
  storage:  no target disk attached
  result:   graphical Proxmox VE 9.2 installer reached
            expected "No Hard Disk found!" page
  install:  not started

physical USB readback:
  bytes read: 1,709,592,576
  SHA-256:   65e5ea078e019ba5017acce6b66f78ea0dfec0672f39eca94772e628f5de8f25
```

See the
[chronological recovery log](../intel-i219v-nvm-recovery-2026-07-23.md) for
the supporting investigation and safety checks.
