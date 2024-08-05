When we are asked to use $ln10^{10}A_s$ in cobaya, with class or classy_sz, we need to adopt the following strategy:

```
    A_s:
        derived: true
        latex: A_s
        value: 'lambda logA: 1e-10*np.exp(logA)'
    
    logA:
        drop: true
        latex: ln10^{10}A_s
        prior:
          max: 3.5
          min: 2.5
        proposal: 0.12
        ref:
          dist: norm
          loc: 3.049
          scale: 1.308E-02
```

This addresses the different naming conventions and requirements between cobaya and class or classy_sz.

The numerical values in this document are given as examples.

