```python
# import necessary modules
# uncomment to get plots displayed in notebook
%matplotlib inline
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from classy import Class
from scipy.optimize import fsolve
from scipy.interpolate import interp1d
import math
```


```python
# esthetic definitions for the plots
font = {'size'   : 16, 'family':'STIXGeneral'}
axislabelfontsize='large'
matplotlib.rc('font', **font)
matplotlib.mathtext.rcParams['legend.fontsize']='medium'
plt.rcParams["figure.figsize"] = [8.0,6.0]
```


```python
common_settings = {'output' : 'tCl',
                   # LambdaCDM parameters
                   'h':0.6781,
                   'omega_b':0.02238280,
                   'omega_cdm':0.1201075,
                   'A_s':2.100549e-09,
                   'n_s':0.9660499,
                   'tau_reio':0.05430842,
                   'thermodynamics_verbose':1
                   }  
##############
#    
# call CLASS
#
###############
M = Class()
M.set(common_settings)
M.compute()
derived = M.get_current_derived_parameters(['tau_rec','conformal_age'])
thermo = M.get_thermodynamics()
print (thermo.keys())
```


```python
tau = thermo['conf. time [Mpc]']
g = thermo['g [Mpc^-1]']
# to make the reionisation peak visible, rescale g by 100 for late times
g[:500] *= 100
#################
#
# start plotting
#
#################
#    
plt.xlim([1.e2,derived['conformal_age']])
plt.xlabel(r'$\tau \,\,\, \mathrm{[Mpc]}$')
plt.ylabel(r'$\mathrm{visibility} \,\,\, g \,\,\, [\mathrm{Mpc}^{-1}]$')
plt.axvline(x=derived['tau_rec'],color='k')
# The conformal time at reionisation  could be extracted from the code.
# But we know it because it is part of the standard output
# when thermodynamics_verbose=1
plt.axvline(x=4255.316282,color='k')
#
# Print functions one by one, saving between each (for slides)
#
plt.semilogx(tau,g,'r',label=r'$\psi$')
```


```python
plt.savefig('thermo.pdf',bbox_inches='tight')
```
