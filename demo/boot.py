from demo.api import is_enabled


def boot_session(bootinfo):
    bootinfo.demo_cutting_enabled = is_enabled()
