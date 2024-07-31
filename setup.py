from setuptools import setup, find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="cmbagent",
    version="0.0.1",
    author="Your Name",
    author_email="your.email@example.com",
    description="A small package for XYZ functionality",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/cmbagent",
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
                      "classy",
                      "camb",
                      "cobaya",
                      "getdist",
                      "openai>=1.37.0",
                      "pyautogen[retrievechat]"],

    extras_require={
        "dev": [
                "tensorflow==2.13.0", 
                "tensorflow-probability==0.21.0", 
                "cosmopower", 
                "mcfit",
            ],
    },
    
    test_suite='tests',
)