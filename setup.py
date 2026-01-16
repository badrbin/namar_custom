from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

# get version from __version__ variable in namar_custom/__init__.py
from namar_custom import __version__ as version

setup(
    name="namar_custom",
    version=version,
    description="Customizations for Namar",
    author="Badr",
    author_email="badrbin@gmail.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires
)
