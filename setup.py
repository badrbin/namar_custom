from setuptools import find_packages, setup

setup(
    name="namar_test",
    version="0.0.1",
    description="Namar ERPNext customizations migrated from live test scripts.",
    author="Namar",
    author_email="badrarroug@namar.net",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
)
