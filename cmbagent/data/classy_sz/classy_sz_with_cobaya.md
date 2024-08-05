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

```
