from namar_kdt.migration import is_enabled


def boot_session(bootinfo):
    bootinfo.namar_kdt_enabled = is_enabled()
