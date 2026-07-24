# Proxmox VE 9.2-1 I219-V recovery installer v1

Unofficial bootable Proxmox VE 9.2-1 installer for the recorded Intel
I219-V `8086:15b8` workstation whose stock e1000e driver refuses to probe
after its NVM checksum validation fails.

The image adds an unsigned, ABI-specific e1000e module for
`7.0.2-6-pve`. Its opt-in `allow_bad_nvm=1` path skips checksum validation
without writing the NIC NVM. The module is integrated into the early initrd,
live installer, and installed kernel package so the workaround is present
during installation and on first boot. The original signed module remains in
place for rollback.

Important:

- This bypasses a hardware integrity check; it does not repair the NVM.
- It was built and validated for the documented `8086:15b8` workstation, not
  as a general Proxmox installer.
- Use legacy BIOS mode or disable Secure Boot module enforcement; the custom
  module is unsigned.
- Keep `7.0.2-6-pve` available. Rebuild the module against matching headers
  before booting a newer PVE kernel.
- Target-disk selection remains the normal destructive Proxmox installer
  workflow.

Known v1 installed-first-boot issue:

- Disable Secure Boot before relying on the installed workaround. On the
  recorded ASRock firmware the setting is under
  `Advanced Mode -> Security -> Secure Boot`; Fast Boot is unrelated.
- The physical install proved that v1 can load the parallel stock e1000e module
  from the installed initramfs even though the correct patched module is
  present in the installed package. The stock driver then fails the NVM
  checksum and no `nic0` appears.
- Follow the
  [current usage guide](https://github.com/Harsh-Upadhayay/homelab-k8s/tree/main/docs/troubleshooting/proxmox-ve-9.2-1-i219v-recovery)
  section **Known v1 installed-first-boot repair** to remove the unusable stock
  copy, regenerate module metadata/initramfs, and prove the fix across a reboot.

Validation included exact source/header pins, embedded artifact hashes, a full
834,762-sector ISO media check, a no-disk QEMU/SeaBIOS boot through the live
installer, and an exact SHA-256 readback after writing the physical USB.

ISO:

```text
file:    proxmox-ve_9.2-1-i219v-recovery.iso
size:    1,709,592,576 bytes
SHA-256: 65e5ea078e019ba5017acce6b66f78ea0dfec0672f39eca94772e628f5de8f25
```

Read the
[usage guide](https://github.com/Harsh-Upadhayay/homelab-k8s/tree/proxmox-ve-9.2-1-i219v-recovery-v1/docs/troubleshooting/proxmox-ve-9.2-1-i219v-recovery)
and
[full troubleshooting log](https://github.com/Harsh-Upadhayay/homelab-k8s/blob/proxmox-ve-9.2-1-i219v-recovery-v1/docs/troubleshooting/intel-i219v-nvm-recovery-2026-07-23.md)
before booting it.
