# Goal

In this example, we demonstrate how to pass parameters to classy_sz and to collect derived parameters

# Intialize


```python
from classy_sz import Class as Class_sz
```

# Baseline parameterization


```python
# the baseline parameterization is:

cosmo_params = {
'omega_b': 0.02242,
'omega_cdm':  0.11933,
'H0': 67.66, # use H0 because this is what is used by the emulators and to avoid any ambiguity when comparing with camb. 
'tau_reio': 0.0561,
'ln10^{10}A_s': 3.047,
'n_s': 0.9665   
}
```


```python
%%time 
classy_sz = Class_sz()
classy_sz.set(cosmo_params)
classy_sz.set({
'output':'tCl,lCl,pCl',
})
classy_sz.compute_class_szfast()
```

    CPU times: user 28.5 ms, sys: 20.4 ms, total: 48.9 ms
    Wall time: 33.1 ms


### collect derived parameters

Here are some examples. More parameters are available.


```python
classy_sz.sigma8()
```




    0.8118792418260571




```python
classy_sz.Neff()
```




    3.044172067466906




```python
classy_sz.get_current_derived_parameters(['A_s'])
```




    {'A_s': 2.105209331337507e-09}




```python
classy_sz.get_current_derived_parameters(['logA'])
```




    {'logA': 3.047}




```python
classy_sz.get_current_derived_parameters(['ln10^{10}A_s'])
```




    {'ln10^{10}A_s': 3.047}




```python
classy_sz.get_current_derived_parameters(['Neff'])
```




    {'Neff': 3.044172067466906}




```python
classy_sz.get_current_derived_parameters(['m_ncdm_in_eV'])
```




    {'m_ncdm_in_eV': 0.02}




```python
classy_sz.get_current_derived_parameters(['Omega_m'])
```




    {'Omega_m': 0.30964144154550644}



### accessing the list of params that have been set


```python
classy_sz.pars
```




    {'output': 'tCl,lCl,pCl',
     'skip_input': 1,
     'skip_background_and_thermo': 1,
     'skip_pknl': 1,
     'skip_pkl': 1,
     'skip_chi': 1,
     'skip_hubble': 1,
     'skip_class_sz': 1,
     'skip_sigma8_at_z': 1,
     'skip_sigma8_and_der': 0,
     'skip_cmb': 0,
     'cosmo_model': 6,
     'N_ur': 0.00441,
     'N_ncdm': 1,
     'deg_ncdm': 3,
     'm_ncdm': 0.02,
     'classy_sz_verbose': 'none',
     'omega_b': 0.02242,
     'n_s': 0.9665,
     'omega_cdm': 0.11933,
     'H0': 67.66,
     'tau_reio': 0.0561,
     'ln10^{10}A_s': 3.047}



# Using different names


```python
# the baseline parameterization is:

cosmo_params = {
'omega_b': 0.02242, # here we can use ombh2 rather than omega_b
'omch2':  0.11933, # here we can use omch2 rather than omega_cdm
'H0': 67.66, # use H0 because this is what is used by the emulators and to avoid any ambiguity when comparing with camb. 
'tau_reio': 0.0561,
'logA': 3.047, # here we can use logA rather than ln10^{10}A_s
'ns': 0.9665  # here we can use ns rather than ns
}
```


```python
%%time 
classy_sz = Class_sz()
classy_sz.set(cosmo_params)
classy_sz.set({
'output':'tCl,lCl,pCl',
})
classy_sz.compute_class_szfast()

print('sigma8:',classy_sz.sigma8())
```

    sigma8: 0.8118792418260571
    CPU times: user 31.2 ms, sys: 16.5 ms, total: 47.7 ms
    Wall time: 38.3 ms


# Passing $\sigma_8$ instead of $A_s$

classy_sz can have $\sigma_8$ as an input parameter. 

In this case the calculation takes a bit longer because it requires a root finding step to get the adequate value of $A_s$. 


```python
cosmo_params_with_sigma_8 = {
'omega_b': 0.02242,
'omega_cdm':  0.11933,
'H0': 67.66, # use H0 because this is what is used by the emulators and to avoid any ambiguity when comparing with camb. 
'tau_reio': 0.0561,
'sigma8': 0.8119,
'n_s': 0.9665   

}
```


```python
%%time 
classy_sz = Class_sz()
classy_sz.set(cosmo_params_with_sigma_8)
classy_sz.set({
'output':'tCl,lCl,pCl',
})
classy_sz.compute_class_szfast()
```

    CPU times: user 38.9 ms, sys: 24.5 ms, total: 63.4 ms
    Wall time: 51.9 ms



```python
classy_sz.get_current_derived_parameters(['A_s'])
```




    {'A_s': 2.1053170341400974e-09}




```python
classy_sz.get_current_derived_parameters(['logA'])
```




    {'logA': 3.047051158830638}




```python
classy_sz.get_current_derived_parameters(['Neff'])
```




    {'Neff': 3.0441720915141284}




```python
classy_sz.get_current_derived_parameters(['m_ncdm_in_eV'])
```




    {'m_ncdm_in_eV': 0.02}



# Passing $\Omega_m$

In this case, we compute omega_cdm from omega_b and Omega_m to match value of Omega_m


```python
from classy_sz import Class as Class_sz

cosmo_params = {
'omega_cdm': 0.11,
'omega_b': 0.023,
'Omega_m':  0.31,
'H0': 67.66, # use H0 because this is what is used by the emulators and to avoid any ambiguity when comparing with camb. 
'tau_reio': 0.0561,
'ln10^{10}A_s': 3.047,
'n_s': 0.9665   
}
```


```python
%%time 
classy_sz = Class_sz()
classy_sz.set(cosmo_params)
classy_sz.set({
'output':'tCl,lCl,pCl',
'skip_input': 1,
})
classy_sz.compute_class_szfast()
```

    CPU times: user 35.7 ms, sys: 23.6 ms, total: 59.3 ms
    Wall time: 40 ms



```python

```


```python

```
