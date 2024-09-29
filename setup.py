from setuptools import setup, find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="cmbagent",
    version="0.0.0",
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
    install_requires=["setuptools", 
                      "wheel", 
                      "numpy>=1.19.0", 
                      "Cython>=0.29.21", 
                      "camb",
                      "cobaya",
                      "getdist",
                      # "pyautogen @ git+https://github.com/CMBAgents/autogen",
                      "ruamel.yaml",
                      "dask[dataframe]",
                      "pandas"
                      ],

    extras_require={
        "dev": [
                "classy",
                "classy_sz",
            ],
         "git_install": ["pyautogen @ git+https://github.com/CMBAgents/autogen"],
    },

    test_suite='tests',
)
