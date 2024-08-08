# How to set the values of fixed parameters in an mcmc analysis with cobaya?

To set the values of parameters that are not varying in an mcmc analysis with cobaya, we adapt the `params` block of the yaml file as follows:

```
params:
    param1:
      prior:
        min: 0.0
        max: 10.0
      ref:
        dist: norm
        loc: 4.1267026E+00
        scale: 1.0
      proposal: 0.1
      latex: param_1

    fixed_param1:
      value: 1.0
      latex: p_1
```

In this example `param1` is a varying parameter, but `fixed_param1` is a fixed parameter.

