# How to deal with $\sigma_8$ and $A_s$ in cobaya?

We can not set both $\sigma_8$ and $A_s$ or $ln10^{10}A_s$ since $\sigma_8$ can be derived from $A_s$ or $ln10^{10}A_s$ and vice-versa.

If we need to vary $A_s$ or $ln10^{10}A_s$ we can follow the following set-up in the `params` block of the yaml file:

```yaml

    A_s:
      prior:
        min: 4e-10
        max: 8e-7
      ref:
        dist: norm
        loc: 
        scale: 1.e-10
      proposal: 5e-10
      latex: 
    
    sigma8: 
        latex: \sigma_8
        derived: true

```

If we need to vary $\sigma_8$ we can use the following setting

```yaml
    sigma_8:
        prior:
          min: 0.1
          max: 2.
        ref:
          dist: norm
          loc: 0.81
          scale: 0.01
        proposal: 0.01
        latex: \sigma_8

    A_s: 
        latex: A_s
        derived: true
```

The numerical values in this document are given as examples.

