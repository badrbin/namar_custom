from setuptools import find_packages, setup

setup(
    name="namar_custom",
    version="0.0.3",
    description="Production-safe Namar ERPNext customizations.",
    author="Namar",
    author_email="badrarroug@namar.net",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
)
