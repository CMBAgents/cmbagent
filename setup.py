# setup.py
from setuptools import setup, find_packages

# Import the version from version.py
version = {}
with open("cmbagent/version.py") as fp:
    exec(fp.read(), version)

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="cmbagent",
    version=version["__version__"],  # Use the version from version.py
    author="CMBAgents Team",
    author_email="bb667@cam.ac.uk",
    description="Multi-agent system for data analysis, made by cosmologists, powered by autogen.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/CMBAgents/cmbagent",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
    install_requires=[
        "setuptools",
        "wheel",
        "numpy>=1.19.0",
        "Cython>=0.29.21",
        "camb",
        "cobaya",
        "getdist",
        "classy_sz",
        "cmbagent_autogen",
        "ruamel.yaml",
        "dask[dataframe]",
        "pandas"
    ],
    test_suite='tests',
)

