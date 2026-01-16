from setuptools import setup, find_packages
import re
import os

# قراءة المتطلبات
with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

# قراءة الإصدار مباشرة من ملف __init__.py كنص لتجنب أخطاء الاستيراد
def get_version():
    init_path = os.path.join("namar_custom", "__init__.py")
    with open(init_path, "r") as f:
        for line in f:
            if line.startswith("__version__"):
                return re.search(r"['\"]([^'\"]+)['\"]", line).group(1)
    return "0.0.1"

setup(
    name="namar_custom",
    version=get_version(),
    description="Customizations for Namar",
    author="Badr",
    author_email="badrbin@gmail.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires
)
