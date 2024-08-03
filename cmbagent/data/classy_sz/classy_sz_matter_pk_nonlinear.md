# Intialize


```python
%matplotlib inline
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from classy_sz import Class as Class_sz
import os
import time





cosmo_params = {
'omega_b': 0.02242,
'omega_cdm':  0.11933,
'H0': 67.66, # use H0 instead of theta_star because this is what is used by the emulators and to avoid any ambiguity when comparing with camb. 
'tau_reio': 0.0561,
'ln10^{10}A_s': 3.047,
'n_s': 0.9665,

}


```

# Compute non-linear matter power spectrum

## Method 1

In this method, $P(k,z)$ is obtained by interpolating a $z$ and $k$ grid. 

The number of points in the $k$ dimension is fixed by the emulators settings. 

The number of points in the $z$ dimension is fixed by the use via the parameter `ndim_redshifts`

To request the non-linear matter power spectrum we set `non_linear` in our parameter dictionnary. Set it "yes". 


```python
%%time 
classy_sz = Class_sz()
classy_sz.set(cosmo_params)
classy_sz.set({
'output':'mPk',
'ndim_redshifts': 25,
'non_linear': 'yes',
})
classy_sz.compute_class_szfast()
```

    CPU times: user 156 ms, sys: 57.9 ms, total: 214 ms
    Wall time: 105 ms



```python
z = 8.3
kmin = 1e-3
kmax = 1e1
nks = 500
ks = np.geomspace(kmin,kmax,nks)
pks = classy_sz.pk(ks,z)
```


```python
plt.plot(ks,pks)
plt.loglog()
```




    []




    
![png](output_6_1.png)
    



```python
# let's time it
%timeit -n 10 classy_sz.compute_class_szfast()
```

    58.8 ms ± 798 µs per loop (mean ± std. dev. of 7 runs, 10 loops each)


Note that this calculation takes longer, because by default we have also computed the linear $P(k)$. 

See hereafter for a faster alternative.

## Method 2

In this method, we first initialize the computation and then compute the pks.

The advantage of this method is that we don't go through the $P(k,z)$ interpolator at each evaluation when we call 
`classy_sz.get_pkl_at_z`


```python
%%time
# initialize computation
classy_sz = Class_sz()
classy_sz.set(cosmo_params)
classy_sz.set({
'output':'mPk',
'non_linear': 'yes',
})
classy_sz.compute_class_szfast()
```

    CPU times: user 366 ms, sys: 99.4 ms, total: 465 ms
    Wall time: 220 ms



```python
%%time
z = 0.3
pks,ks = classy_sz.get_pknl_at_z(z,params_values_dict = cosmo_params)
```

    CPU times: user 2.78 ms, sys: 1.73 ms, total: 4.51 ms
    Wall time: 2.68 ms



```python
plt.plot(ks,pks)
plt.loglog()
```




    []




    
![png](output_12_1.png)
    



```python
# let's time it 
%timeit -n 10 classy_sz.get_pknl_at_z(z,params_values_dict = cosmo_params)
```

    1.1 ms ± 60.1 µs per loop (mean ± std. dev. of 7 runs, 10 loops each)

