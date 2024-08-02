# How to use classy_sz with cobaya?

To use classy_sz with cobaya we adopt the following settings in the yaml file:

```
theory:
    classy_szfast.classy_sz.classy_sz:
        extra_args:
            output: tCl, lCl, pCl
```

where in this example we set the output that classy_sz should compute, as an example.

