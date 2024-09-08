# How to set up the likelihood for ACT DR6 Lensing in cobaya

To set up the likelihood for ACT DR6 Lensing in cobaya, you need to use the dictionnary: 

```python

    "likelihood": {
        "act_dr6_lenslike.ACTDR6LensLike": {
            "mock": False,
            "nsims_act": 792.0,
            "nsims_planck": 400.0,
            "no_like_corrections": True,
            "lens_only": True,
            "trim_lmax": 2998,
            "variant": "actplanck_baseline",
            "apply_hartlap": True,
            "limber": False,
            "nz": 100,
            "kmax": 10,
            "scale_cov": None,
            "type": [],
            "speed": -1,
            "stop_at_error": True,
            "version": None,
            "lmax": 4000,
            "input_params": [],
            "output_params": []
        }
    }
```

