# How to add derived parameters in cobaya

In the params block, you can add derived parameters as follows:

```
  omegam:
    latex: \Omega_\mathrm{m}
    derived: true
  omegamh2:
    derived: 'lambda omegam, H0: omegam*(H0/100)**2'
    latex: \Omega_\mathrm{m} h^2
```

