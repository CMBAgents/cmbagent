```python
# import necessary modules
%matplotlib inline
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from classy import Class
from scipy.optimize import fsolve
import math
```


```python
############################################
#
# Varying parameter (others fixed to default)
#
var_name = 'm_ncdm'
var_min = 1.e-10
var_max = var_min + 0.3333333
var_num = 5
var_legend = r'$\sum m_\nu$'
var_figname = 'mnu'
#
# Instead of ficing h=0.67556, we will fix here theta_s.
# For the reference LambdaCDM model we get 100*theta_s = 1.042167.
# We will impose this for whatever mass (then CLASS adjusts h automatically)
#
#############################################
#
# Fixed settings
#
common_settings = {'output':'tCl,pCl,lCl,mPk',
                   'lensing':'yes',
                   # LambdaCDM parameters
                   #'100*theta_s':1.042167,
                   'omega_b':0.022032,
                   #'omega_cdm':0.12038,
                   'A_s':2.215e-9,
                   'n_s':0.9619,
                   'tau_reio':0.0925,
                   # Take fixed value for primordial Helium (instead of automatic BBN adjustment)
                   'YHe':0.246,
                   # other output and precision parameters
                   'N_ncdm':1,
                   'deg_ncdm':3.,
                   'N_ur':0.00641,
                   'P_k_max_1/Mpc':3.0,
                   'l_switch_limber':9}
                   #'background_verbose':1} 

#    
slightly_better_precision_settings = {
                                    'tol_thermo_integration':1.e-5,
                                    'tol_perturb_integration':1.e-6,
                                    'l_logstep':1.026,
                                    'l_linstep':25,
                                    'hyper_flat_approximation_nu':7000.,
                                    'transfer_neglect_delta_k_S_t0':0.17,
                                    'transfer_neglect_delta_k_S_t1':0.05,
                                    'transfer_neglect_delta_k_S_t2':0.17,
                                    'transfer_neglect_delta_k_S_e':0.13,
                                    'delta_l_max':1000,
                                    }
#    
much_better_precision_settings = {
                            'recfast_Nz0':100000,
                            'tol_thermo_integration':1.e-5,
                            'recfast_x_He0_trigger_delta':0.01,
                            'recfast_x_H0_trigger_delta':0.01,
                            'evolver':0,
                            'k_min_tau0':0.002,
                            'k_max_tau0_over_l_max':3.,
                            'k_step_sub':0.015,
                            'k_step_super':0.0001,
                            'k_step_super_reduction':0.1,
                            'start_small_k_at_tau_c_over_tau_h': 0.0004,
                            'start_large_k_at_tau_h_over_tau_k': 0.05,
                            'tight_coupling_trigger_tau_c_over_tau_h':0.005,
                            'tight_coupling_trigger_tau_c_over_tau_k':0.008,
                            'start_sources_at_tau_c_over_tau_h': 0.006,
                            'l_max_g':50,
                            'l_max_pol_g':25,
                            'l_max_ur':50,
                            'tol_perturb_integration':1.e-6,
                            'perturb_sampling_stepsize':0.01,
                            'radiation_streaming_approximation':2,
                            'radiation_streaming_trigger_tau_over_tau_k':240.,
                            'radiation_streaming_trigger_tau_c_over_tau':100.,
                            'ur_fluid_approximation':2,
                            'ur_fluid_trigger_tau_over_tau_k':50.,
                            'l_logstep':1.026,
                            'l_linstep':25,
                            'hyper_sampling_flat':12.,
                            'hyper_nu_sampling_step':10.,
                            'hyper_phi_min_abs':1.e-10,
                            'hyper_x_tol':1.e-4,
                            'hyper_flat_approximation_nu':1.e6,
                            'q_linstep':0.20,
                            'q_logstep_spline':20.,
                            'q_logstep_trapzd':0.5,
                            'q_numstep_transition':250,
                            'transfer_neglect_delta_k_S_t0':0.17,
                            'transfer_neglect_delta_k_S_t1':0.05,
                            'transfer_neglect_delta_k_S_t2':0.17,
                            'transfer_neglect_delta_k_S_e':0.13,
                            'neglect_CMB_sources_below_visibility':1.e-30,
                            'transfer_neglect_late_source':3000.,
                            'l_switch_limber':40.,
                            'accurate_lensing':1,
                            'num_mu_minus_lmax':1000.,
                            'delta_l_max':1000.,
                            } 
#
sligthly_better_precision_ncdm = {
                        'tol_ncdm_bg':1.e-10,
                        #'l_max_ncdm':50,
                        'ncdm_fluid_approximation':3,
                        'ncdm_fluid_trigger_tau_over_tau_k':51.,
                        'tol_ncdm_synchronous':1.e-10,
                                }
#
much_better_precision_ncdm = {
                            'tol_ncdm_bg':1.e-10,
                            'l_max_ncdm':50,
                            'ncdm_fluid_approximation':3,
                            'ncdm_fluid_trigger_tau_over_tau_k':51.,
                            'tol_ncdm_synchronous':1.e-10,
                            }
```


```python
#
# loop over varying parameter values
#
M = {}
h=[]
omega_nu=[]
#
for i in range(var_num):
    #
    # deal with varying parameters:
    #
    var = var_min + (var_max-var_min)*i/(var_num-1.)
    #
    #
    #    
    # call CLASS
    #
    M[i] = Class()
    M[i].set(common_settings)
    #M[i].set(slightly_better_precision_settings)
    #M[i].set(much_better_precision_settings)
    #M[i].set(sligthly_better_precision_ncdm)
    #M[i].set(much_better_precision_ncdm)
    M[i].set({var_name:var})
    M[i].set({'omega_cdm':0.12038,'100*theta_s':1.042167})
    M[i].compute()
    derived = M[i].get_current_derived_parameters(['z_reio','z_rec'])
    print 'z_reio = ',derived['z_reio']
    print 'z_rec = ',derived['z_rec']
    print 'z_eq = ',(M[i].Omega_m()/M[i].Omega_r())
    print 'Omega_Lambda = ',(M[i].Omega_Lambda())
    print 'h = ',M[i].h()
    print 'omega_nu = ',(M[i].Omega_nu*M[i].h()*M[i].h())
    h.append(M[i].h())
    omega_nu.append(M[i].Omega_nu*M[i].h()*M[i].h())
```


      Cell In[3], line 28
        print 'z_reio = ',derived['z_reio']
        ^
    SyntaxError: Missing parentheses in call to 'print'. Did you mean print(...)?




```python
print omega_nu
#
# loop over varying parameter values
#
M_bis = {}
h_bis=[]
#
for i in range(var_num):
    #
    # deal with varying parameters:
    #
    var = var_min + (var_max-var_min)*i/(var_num-1.)
    #
    #
    #    
    # call CLASS
    #
    M_bis[i] = Class()
    M_bis[i].set(common_settings)
    #M_bis[i].set(slightly_better_precision_settings)
    #M_bis[i].set(much_better_precision_settings)
    #M_bis[i].set(sligthly_better_precision_ncdm)
    #M_bis[i].set(much_better_precision_ncdm)
    M_bis[i].set({var_name:var})
    M_bis[i].set({'omega_cdm':(0.12038-omega_nu[i]),'h':h[0]})
    M_bis[i].compute()
    derived_bis = M_bis[i].get_current_derived_parameters(['z_reio','z_rec'])
    print 'z_reio = ',derived_bis['z_reio']
    print 'z_rec = ',derived_bis['z_rec']
    print 'z_eq = ',(M_bis[i].Omega_m()/M_bis[i].Omega_r())
    print 'Omega_Lambda = ',(M_bis[i].Omega_Lambda())
    print 'h = ',M_bis[i].h()
    h_bis.append(M_bis[i].h())
```

    [1.7071326916207897e-05, 0.0026841064870704065, 0.005368106872655627, 0.008052130834815801, 0.010736160691406971]
    z_reio =  11.3476867676
    z_rec =  1089.20889623
    z_eq =  5750.35080442
    Omega_Lambda =  0.68785844082
    h =  0.6755154
    z_reio =  11.3448791504
    z_rec =  1089.04383162
    z_eq =  5750.35080443
    Omega_Lambda =  0.68785844082
    h =  0.6755154
    z_reio =  11.3434753418
    z_rec =  1088.90910386
    z_eq =  5750.35080441
    Omega_Lambda =  0.68785844082
    h =  0.6755154
    z_reio =  11.3434753418
    z_rec =  1088.7990698
    z_eq =  5750.35080442
    Omega_Lambda =  0.68785844082
    h =  0.6755154
    z_reio =  11.3434753418
    z_rec =  1088.70812366
    z_eq =  5750.35080444
    Omega_Lambda =  0.687858440819
    h =  0.6755154



```python
# esthetic definitions for the plots
font = {'size'   : 24, 'family':'STIXGeneral'}
axislabelfontsize='large'
matplotlib.rc('font', **font)
matplotlib.mathtext.rcParams['legend.fontsize']='medium'
plt.rcParams["figure.figsize"] = [8.0,6.0]
```


```python
#h = [0.6755154, 0.6537643, 0.6320286, 0.6119767, 0.5935057]
print h
print h[0]
#############################################
#
# arrays for output
#
kvec = np.logspace(-4,np.log10(3),1000)
twopi = 2.*math.pi
#
# Create figures
#
fig_Pk, ax_Pk = plt.subplots()
fig_TT, ax_TT = plt.subplots()
#
# loop over varying parameter values
#
ll = {}
clM = {}
clTT = {}
clM_unlensed = {}
clTT_unlensed = {}
pkM = {}
clM_bis = {}
clTT_bis = {}
clM_unlensed_bis = {}
clTT_unlensed_bis = {}
pkM_bis = {}
legarray = []
#
for i in range(var_num):
    #
    # deal with varying parameters:
    #
    var = var_min + (var_max-var_min)*i/(var_num-1.)
    #
    #
    # deal with colors and legends
    #
    if i == 0:
        var_color = 'k'
        var_alpha = 1.
    else:
        #var_color = 'r'
        #var_alpha = 1.*i/(var_num-1.)
        var_color = plt.cm.Reds(0.8*i/(var_num-1))
    #
    # get Cls
    #
    clM[i] = M[i].lensed_cl(2500)
    ll[i] = clM[i]['ell'][2:]
    clTT[i] = clM[i]['tt'][2:]
    clM_unlensed[i] = M[i].raw_cl(2500)
    clTT_unlensed[i] = clM_unlensed[i]['tt'][2:]
    #
    clM_bis[i] = M_bis[i].lensed_cl(2500)
    clTT_bis[i] = clM_bis[i]['tt'][2:]
    clM_unlensed_bis[i] = M_bis[i].raw_cl(2500)
    clTT_unlensed_bis[i] = clM_unlensed_bis[i]['tt'][2:]
    #
    # store P(k) for common k values
    #
    pkM[i] = []
    print 'h = ',h[i]
    khvec = kvec*h[i] # This is k in 1/Mpc
    for kh in khvec:
        #pkM[i].append(M[i].pk(kh,0.)) 
        pkM[i].append(M[i].pk(kh,0.)*h[i]**3) 
        # M[i].pk(kh,0.) is P(k) in Mpc**3
        # pkM[i] is P(k)in (Mpc/h)**3  
    pkM_bis[i] = []
    print 'h = ',h_bis[i]
    khvec_bis = kvec*h_bis[i] # This is k in 1/Mpc
    for kh_bis in khvec_bis:
        #pkM[i].append(M[i].pk(kh,0.)) 
        pkM_bis[i].append(M_bis[i].pk(kh_bis,0.)*h_bis[i]**3) 
        # M[i].pk(kh,0.) is P(k) in Mpc**3
        # pkM[i] is P(k)in (Mpc/h)**3      
    #    
    # plot P(k)
    #
    if i == 0:
        ax_Pk.semilogx(kvec,np.array(pkM[i])/np.array(pkM[0]),
                   color=var_color,#alpha=var_alpha,
                    linestyle='-')
    else:
        #ax_Pk.semilogx(kvec,np.array(pkM[i])/np.array(pkM[0]),
        #           color=var_color,#alpha=var_alpha,
        #               linestyle='-',
        #              label = r'$\Sigma m_\nu=%g \mathrm{eV}$'%(3.*var))
        ax_Pk.semilogx(kvec,np.array(pkM_bis[i])/np.array(pkM_bis[0]),
                   color=var_color,#alpha=var_alpha,
                       linestyle='--',
                      label = r'$\Sigma m_\nu=%g \mathrm{eV}$'%(3.*var))
    #
    # plot C_l^TT
    #
    if i == 0:
        ax_TT.semilogx(ll[i],clTT[i]/clTT[0],
                   color=var_color,#alpha=var_alpha,
                       linestyle='-')
    else:
        ax_TT.semilogx(ll[i],clTT[i]/clTT[0],
                   color=var_color,#alpha=var_alpha,
                       linestyle='-',
                      label = r'$\Sigma m_\nu=%g \mathrm{eV}$'%(3.*var))
        ax_TT.semilogx(ll[i],clTT_unlensed[i]/clTT_unlensed[0],
                   color=var_color,alpha=var_alpha,linestyle=':',
        #
```


      File "<ipython-input-6-b5762bad944a>", line 109
        #
         ^
    SyntaxError: invalid syntax




```python
#h = [0.6755154, 0.6537643, 0.6320286, 0.6119767, 0.5935057]
print h
print h[0]
#############################################
#
# arrays for output
#
kvec = np.logspace(-4,np.log10(3),1000)
twopi = 2.*math.pi
#
# Create figures
#
fig_Pk, ax_Pk = plt.subplots()
fig_TT, ax_TT = plt.subplots()
#
# loop over varying parameter values
#
ll = {}
clM = {}
clTT = {}
clM_unlensed = {}
clTT_unlensed = {}
pkM = {}
clM_bis = {}
clTT_bis = {}
clM_unlensed_bis = {}
clTT_unlensed_bis = {}
pkM_bis = {}
legarray = []
#
for i in range(var_num):
    #
    # deal with varying parameters:
    #
    var = var_min + (var_max-var_min)*i/(var_num-1.)
    #
    #
    # deal with colors and legends
    #
    if i == 0:
        var_color = 'k'
        var_alpha = 1.
    else:
        #var_color = 'r'
        #var_alpha = 1.*i/(var_num-1.)
        var_color = plt.cm.Reds(0.8*i/(var_num-1))
    #
    # get Cls
    #
    clM[i] = M[i].lensed_cl(2500)
    ll[i] = clM[i]['ell'][2:]
    clTT[i] = clM[i]['tt'][2:]
    clM_unlensed[i] = M[i].raw_cl(2500)
    clTT_unlensed[i] = clM_unlensed[i]['tt'][2:]
    #
    clM_bis[i] = M_bis[i].lensed_cl(2500)
    clTT_bis[i] = clM_bis[i]['tt'][2:]
    clM_unlensed_bis[i] = M_bis[i].raw_cl(2500)
    clTT_unlensed_bis[i] = clM_unlensed_bis[i]['tt'][2:]
    #
    # store P(k) for common k values
    #
    pkM[i] = []
    print 'h = ',h[i]
    khvec = kvec*h[i] # This is k in 1/Mpc
    for kh in khvec:
        #pkM[i].append(M[i].pk(kh,0.)) 
        pkM[i].append(M[i].pk(kh,0.)*h[i]**3) 
        # M[i].pk(kh,0.) is P(k) in Mpc**3
        # pkM[i] is P(k)in (Mpc/h)**3  
    pkM_bis[i] = []
    print 'h = ',h_bis[i]
    khvec_bis = kvec*h_bis[i] # This is k in 1/Mpc
    for kh_bis in khvec_bis:
        #pkM[i].append(M[i].pk(kh,0.)) 
        pkM_bis[i].append(M_bis[i].pk(kh_bis,0.)*h_bis[i]**3) 
        # M[i].pk(kh,0.) is P(k) in Mpc**3
        # pkM[i] is P(k)in (Mpc/h)**3      
    #    
    # plot P(k)
    #
    if i == 0:
        ax_Pk.semilogx(kvec,np.array(pkM[i])/np.array(pkM[0]),
                   color=var_color,#alpha=var_alpha,
                    linestyle='-')
    else:
        ax_Pk.semilogx(kvec,np.array(pkM[i])/np.array(pkM[0]),
                   color=var_color,#alpha=var_alpha,
                       linestyle='-',
                      label = r'$\Sigma m_\nu=%g \mathrm{eV}$'%(3.*var))
        ax_Pk.semilogx(kvec,np.array(pkM_bis[i])/np.array(pkM_bis[0]),
                   color=var_color,#alpha=var_alpha,
                       linestyle='--')
                      #label = r'$\Sigma m_\nu=%g \mathrm{eV}$'%(3.*var))
    #
    # plot C_l^TT
    #
    if i == 0:
        ax_TT.semilogx(ll[i],clTT[i]/clTT[0],
                   color=var_color,#alpha=var_alpha,
                       linestyle='-')
    else:
        ax_TT.semilogx(ll[i],clTT[i]/clTT[0],
                   color=var_color,#alpha=var_alpha,
                       linestyle='-',
                      label = r'$\Sigma m_\nu=%g \mathrm{eV}$'%(3.*var))
        #ax_TT.semilogx(ll[i],clTT_unlensed[i]/clTT_unlensed[0],
        #           color=var_color,alpha=var_alpha,linestyle=':',
        #              label = r'$M_\nu=%g \mathrm{eV}$'%(3.*var))
    #

    
#
# output of P(k) figure
#
ax_Pk.set_xlim([1.e-4,3.])
ax_Pk.set_ylim([0.15,1.03])
ax_Pk.set_xlabel(r'$k \,\,\,\, [h^{-1}\mathrm{Mpc}]$')
ax_Pk.set_ylabel(r'$P(k)/P(k)(\Sigma m_\nu=0)$')
ax_Pk.legend(loc='lower left',fontsize=22)
fig_Pk.tight_layout()
#fig_Pk.savefig('rpp-ratio-%s-Pk.eps' % var_figname,format='eps')#,layout='tight')
#fig_Pk.savefig('rpp-ratio-%s-Pk-dashed.pdf' % var_figname,format='pdf')#,layout='tight')
fig_Pk.savefig('rpp-ratio-%s-Pk.pdf' % var_figname,format='pdf')#,layout='tight')
#
# output of C_l^TT figure
#      
ax_TT.set_xlim([2,2500])
ax_TT.set_ylim([0.850,1.025])
ax_TT.set_xlabel(r'$\mathrm{Multipole} \,\,\,\, \ell$')
ax_TT.set_ylabel(r'$C_\ell^\mathrm{TT}/C_\ell^\mathrm{TT}(\Sigma m_\nu=0)$')
ax_TT.legend(loc='lower right',fontsize=22)
fig_TT.tight_layout()
#fig_TT.savefig('rpp-ratio-%s-cltt.eps' % var_figname,format='eps')#,layout='tight')
fig_TT.savefig('rpp-ratio-%s-cltt.pdf' % var_figname,format='pdf')#,layout='tight')
#
# output of C_l^EE figure
#    
#ax_EE.set_xlim([2,2500])
#ax_EE.set_xlabel(r'$\ell$')
#ax_EE.set_ylabel(r'$[\ell(\ell+1)/2\pi]  C_\ell^\mathrm{EE}$')
#ax_EE.legend(legarray,loc='lower right')
#fig_EE.tight_layout()
#fig_EE.savefig('spectra_%s_clee.pdf' % var_figname)
#
# output of C_l^pp figure
#   
#ax_PP.set_xlim([10,2500])
#ax_PP.set_xlabel(r'$\ell$')
#ax_PP.set_ylabel(r'$[\ell^2(\ell+1)^2/2\pi]  C_\ell^\mathrm{\phi \phi}$')
#ax_PP.legend(legarray)
#fig_PP.tight_layout()
#fig_PP.savefig('spectra_%s_clpp.pdf' % var_figname)
```

    [0.6755154, 0.6537643, 0.6320286, 0.6119767, 0.5935057]
    0.6755154
    h =  0.6755154
    h =  0.6755154
    h =  0.6537643
    h =  0.6755154
    h =  0.6320286
    h =  0.6755154
    h =  0.6119767
    h =  0.6755154
    h =  0.5935057
    h =  0.6755154



    
![png](output_6_1.png)
    



    
![png](output_6_2.png)
    



```python

```


```python

```
