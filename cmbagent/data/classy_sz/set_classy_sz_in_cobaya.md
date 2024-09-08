# How to set classy_sz theory block with cobaya?

The theory block should be set according as 

```python
"theory": {
        "classy_szfast.classy_sz.classy_sz": {
            "extra_args": { # here some extra args if needed
                           }
  
                }
    }
```
where classy_sz is called through **classy_szfast.classy_sz.classy_sz**


# Classy_sz whth BAO likelihoods

With BAO likelihoods, we need to set the classy_sz theory block as: 

```python
theory:
  classy_szfast.classy_sz.classy_sz:
    baos: true
    extra_args: {}
```

setting baos: true