from setuptools import setup, find_packages
import re

with open("quale/__init__.py") as f:
    ver = re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)

setup(
    name="quale-cli",
    version=ver,
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "typer>=0.9",
        "typing-extensions>=4.0",
    ],
    entry_points={
        "console_scripts": [
            "quale=quale.cli:main",
        ],
    },
)
