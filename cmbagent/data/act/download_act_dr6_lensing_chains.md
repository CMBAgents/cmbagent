# How to download the original ACT DR6 Lensing chains



**IMPORTANT**: To download the ACT DR6 lensing chains, you can write a bash script download_chains.sh:

```bash
#!/bin/bash

# Define the base URL, subdirectory, and the root name
base_url="https://portal.nersc.gov/project/act/act_dr6_lensing/chains"
subdir="dr6-lensing"
root="lcdm_actplanck_baseline_167926925701"

# Fetch the list of files in the directory and filter for .txt and .updated.yaml files
wget -q -O - "${base_url}/${subdir}/" | grep -oE "${root}\.(\d+\.txt|updated\.yaml)" | while read -r file; do
  wget -O "${file}" "${base_url}/${subdir}/${file}"
done
```

win this case `root` is set to `lcdm_actplanck_baseline_167926925701` and `subdir` to `dr6-lensing`. There are more options, as listed below. **ASK Admin which one they want to use if they haven't specified it!** **Check with Admin if the download command makes sense.**

and then download the chains with

```bash
chmod +x download_chains.sh
./download_chains.sh
```

**Make sure you set `chains_dir` correctly, ask Admin if unsure**



Inofrmation about ACT DR6 Lensing Chains
======================

IMPORTANT: This is a pre-release directory. The filenames of the final chains will not be the same.  It contains a large number of redundant run cases, which need to be trimmed out. I will list some of the main chain roots used in the DR6 lensing papers below. This is not a complete list yet.


subdir="dr6-lensing"
------------

This contains ACT CMB lensing chains (from Cobaya), in a format suitable for loading with getdist. They are Metropolis-Hastings runs with 8 chains each.

Paper run chain roots (cite 2304.05203 and 2304.05202):
* ACT Baseline (includes BAO) LCDM: lcdm_act_baseline_rerun_167934952674
* ACT+Planck Baseline (includes BAO) LCDM: lcdm_actplanck_baseline_167926925701
* ACT+Planck Extended (includes BAO) LCDM: lcdm_actplanck_extended_167950509669
* Planck PR4 high-ell TTTEEE + SRoll2 low-ell EE + Planck PR3 low-ell TT (cite 2205.10869, 1907.12875 and 1908.09856): planck_pr4_with_sroll2_167865166442


subdir="gal-reanalysis-des-kids"
---------------

This contains re-analyses of DES and KiDS (from CosmoSIS). They need to be converted to be used with getdist. You can use this branch of tensiometer for that: https://github.com/ACTCollaboration/tensiometer/tree/multiple_chains .

Paper run chain roots:
* KiDS-1000 (cite 2208.07179 and 2007.15633) : kids-shear-bao-mh-nu1
* DES-Y3 (cite 2105.13544 and 2105.13543): des-y3-shear-bao-mh-nu1


subdir="gal-reanalysis-hsc"
-------------------

Same as above but for HSC. These were run by Roohi Dalal and Xiangchong Li from the HSC team.

Paper run chain roots:
* HSC-Y3 Real Space (cite 2304.00702): cosmicShear_2pcfs_bao_actConfig
* HSC-Y3 Fourier Space (cite 2304.00701): act_setup_bao

