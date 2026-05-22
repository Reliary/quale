from setuptools import setup, find_packages

setup(
    name="vocab-cli",
    version="0.1.0",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "typer>=0.9",
        "typing-extensions>=4.0",
    ],
    entry_points={
        "console_scripts": [
            "vocab=vocab.cli:main",
        ],
    },
)
