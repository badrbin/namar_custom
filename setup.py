from setuptools import setup, find_packages

setup(
    name="namar_custom",
    version="0.0.1",
    description="Customizations for Namar",
    author="Badr",
    author_email="badrbin@gmail.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=["frappe"]
)
