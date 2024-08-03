# How to use classy_sz with cobaya?


```python
import yaml

# Define the YAML configuration
cobaya_config = {
    "likelihood": {
        "act_dr6_lenslike.ACTDR6LensLike": {} # here a likelihood 
    },
    "params": {
        "ombh2": 0.022383,
        "omch2": 0.1181084544,
        "H0": 67.32,
        "tau": 0.0543,
        "n_s": 0.96605,
        "sigma8": 0.8
    },
    "theory": {
        "classy_szfast.classy_sz.classy_sz": {
            "extra_args": { # here some extra args if needed
            }
        }
    },
    "sampler": {
        "evaluate": {
        }
    }
}

with open("cobaya_evaluate_config_with_classy_sz.yaml", "w") as file:
    yaml.dump(cobaya_config, file)

```


```python
# Run Cobaya with the configuration file
import os
os.system("cobaya-run -f cobaya_evaluate_config_with_classy_sz.yaml")
```

    [output] Output to be read-from/written-into folder '.', with prefix 'cobaya_evaluate_config_with_classy_sz'
    [output] Found existing info files with the requested output prefix: 'cobaya_evaluate_config_with_classy_sz'
    [output] Will delete previous products ('force' was requested).
    [classy_szfast.classy_sz.classy_sz] Initialized!


    /Users/boris/Work/CLASS-SZ/SO-SZ/act_dr6_lenslike/act_dr6_lenslike/act_dr6_lenslike.py:416: UserWarning: Hartlap correction to cinv: 0.9860935524652339
      warnings.warn(f"Hartlap correction to cinv: {hartlap_correction}")


    Loading ACT DR6 lensing likelihood v1.2...
    [evaluate] *WARNING* No sampled parameters requested! This will fail for non-mock samplers.
    [evaluate] Initialized!
    [evaluate] Looking for a reference point with non-zero prior.
    [evaluate] Reference point:
       
    [evaluate] Evaluating prior and likelihoods...
    [evaluate] log-posterior  = -8.39425
    [evaluate] log-prior      = 0
    [evaluate]    logprior_0 = 0
    [evaluate] log-likelihood = -8.39425
    [evaluate]    chi2_act_dr6_lenslike.ACTDR6LensLike = 16.7885
    [evaluate] Derived params:





    0




```python

```
