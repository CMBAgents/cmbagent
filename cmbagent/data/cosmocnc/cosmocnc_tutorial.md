# cosmocnc tutorial

In this tutorial we will illustrate the main computational capabilities of cosmocnc using a Simons-Observatory-like cluster catalogue with two mass observables, the tSZ signal-to-noise and the CMB lensing signal-to-noise.

## Setting input parameters

This is an example for a typical configuration for the Simons Observatory galaxy catalogue.


```python
import cosmocnc

cnc_params = cosmocnc.cnc_params_default
scal_rel_params = cosmocnc.scaling_relation_params_default
cosmo_params = cosmocnc.cosmo_params_default

#Catalogue and observables

cnc_params["cluster_catalogue"] = "SO_sim_0"
cnc_params["observables"] = [["q_so_sim"],["p_so_sim"]]
cnc_params["obs_select"] = "q_so_sim"

#Mass and redshift range

cnc_params["M_min"] = 1e13
cnc_params["M_max"] = 1e16
cnc_params["z_min"] = 0.01
cnc_params["z_max"] = 3.

#Selection observable range

cnc_params["obs_select_min"] = 5. #selection threshold
cnc_params["obs_select_max"] = 200.

#Precision parameters

cnc_params["n_points"] = 16384 #number of points in which the mass function at each redshift (and all the convolutions) is evaluated
cnc_params["n_points_data_lik"] = 2048 #number of points in which the mass function at each redshift (and all the convolutions) is evaluated
cnc_params["n_z"] = 100
cnc_params["sigma_mass_prior"] = 10
cnc_params["delta_m_with_ref"] = True
cnc_params["scalrel_type_deriv"] = "numerical"
cnc_params["downsample_hmf_bc"] = 2
cnc_params["compute_abundance_matrix"] = True

#Parallelisation

cnc_params["number_cores_hmf"] = 1
cnc_params["number_cores_abundance"] = 1
cnc_params["number_cores_data"] = 8
cnc_params["parallelise_type"] = "redshift"

#Cosmology parameters

cnc_params["cosmology_tool"] = "classy_sz"
cnc_params["cosmo_param_density"] = "critical"
cnc_params["cosmo_model"] = "lcdm"

#Parameters for the binned likelihood

cnc_params["binned_lik_type"] = "z_and_obs_select"
cnc_params["bins_edges_z"] = np.linspace(cnc_params["z_min"],cnc_params["z_max"],9)
cnc_params["bins_edges_obs_select"] = np.exp(np.linspace(np.log(cnc_params["obs_select_min"]),np.log(cnc_params["obs_select_max"]),7))

#Stacked data, set to False for now

cnc_params["stacked_likelihood"] = False
cnc_params["stacked_data"] = ["p_so_sim_stacked"] #list of stacked data
cnc_params["compute_stacked_cov"] = True


cnc_params["data_lik_from_abundance"] = False
cnc_params["likelihood_type"] = "unbinned"

scal_rel_params["corr_lnq_lnp"] = 0.
scal_rel_params["bias_sz"] = 0.8
```

## Code initialisation

To initialise cosmocnc, do this:

```python
number_counts = cosmocnc.cluster_number_counts()

number_counts.cnc_params = cnc_params
number_counts.scal_rel_params = scal_rel_params
number_counts.cosmo_params = cosmo_params

number_counts.initialise()

```

## How to compute the cluster abundance

In order to compute the cluster abundance across the selection mass observable (SZ signal-to-noise, or SNR) and redshift, do this.


```python
number_counts.get_number_counts()

dn_dz = number_counts.n_z #This is the number counts as a function of redshift
dn_dSNR = number_counts.n_obs #This is the number counts as a function of the selection selection mass observable (in this case, the SZ signa-to-noise aka SNR)

z = number_counts.redshift_vec #This is the redshift values at which dn_dz is evaluated
SNR = number_counts.obs_select_vec #This is the selection mass observable values (here, the SZ signal-to-noise) at which dn_dSNR is evaluated

```

To now plot this cluster abundance, do this:

```python
pl.rc('text', usetex=True)
pl.rc('font', family='serif')

pl.semilogx(SNR,dn_dSNR,color="tab:blue")
pl.xlabel("$SNR$")
pl.ylabel("$dN / d SNR$")
pl.axvline(x=5.,color="k")
pl.axhline(y=0.,color="k")
pl.title("Cluster abundance across SNR (selection observable)")
pl.ylim([0,7000])
pl.show()

pl.plot(z,dn_dz,color="tab:blue")
pl.xlabel("$z$")
pl.ylabel("$dN / dz$")
pl.axvline(x=0.,color="k")
pl.axhline(y=0.,color="k")
pl.ylim(0,24000)
pl.title("Cluster abundance across redshift")
pl.show()

```

In order to get the two-dimensional cluster abundance across both the selection observable and redshift, do this:

```python
abundance_matrix = np.flip(number_counts.abundance_matrix,axis=0)
```

You can then plot it on a 2d map as this:

```python
import matplotlib.ticker as mticker
from matplotlib.ticker import ScalarFormatter
from matplotlib.ticker import StrMethodFormatter

fig = pl.figure()
ax = fig.add_subplot()

im = ax.imshow(abundance_matrix,extent=[SNR[0],SNR[-1],z[0],z[-1]],cmap="cividis",aspect=0.5)
fig.colorbar(im,ax=ax)
ax.contour(np.flip(abundance_matrix,axis=0),colors=["k","k","k","k"],extent=[SNR[0],SNR[-1],z[0],z[-1]])
ax.set_xscale("log")
ax.set_xlabel("$q_{\mathrm{obs}}$")
ax.set_ylabel("$z$")
ax.set_xlim([5.,30.])
ax.set_ylim([0.01,1.5])
ax.xaxis.set_minor_formatter(mticker.ScalarFormatter())
ax.xaxis.set_major_formatter(ScalarFormatter())
ax.xaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
fig.tight_layout()
pl.show()

```

To retrieve the predicted total number of clusters in the sample and the total actual number of clusters in the sample, do this:

```python
n_tot_theory = number_counts.n_tot #Predicted total number of clusters in the sample.
n_tot_obs = number_counts.catalogue.n_tot #Actual total number of clusters in the sample

print("Predicted total number of clusters in the catalogue:",n_tot_theory,"+-",np.sqrt(n_tot_theory))
print("Observed total number of clusters in the catalogue:",n_tot_obs)
```

Here, the quoted error on the theoretical prediction is the associated Poisson error.

## How to compute the binned cluster abundance

In order to compute the cluster abundance as a function of the selection mass observable (here, the SZ signal-to-noise) and redshift, do this. For, call the binned likelihood. Note that the bin edges were already defined when we set the parameters of cosmocnc.


```python
cnc_params["likelihood_type"] = "binned" #Update the cosmocnc parameters to compute the binned likelihood.
cnc_params["obs_select_min"] = 5.000 #Set the selection threshold.

number_counts.cnc_params = cnc_params

log_lik = number_counts.get_log_lik() #Evaluate the binned likelihood

bins_centres_z = number_counts.bins_centres_z #Redhisft (z) bins centres
bins_centres_snr = number_counts.bins_centres_obs #Selection mass observable (SZ signal-to-noise in this case) bins centres

n_binned_theory = number_counts.n_binned #Theoretical prediction for the number counts in the two-dimensional selection mass observable  - redshift bins
n_binned_obs = number_counts.n_binned_obs #Actual number counts in the catalogue in the same two-dimensional bins

n_tot_bins_theory = np.sum(n_binned_theory) #Theoretical prediction for the total number of clusters in the bins
n_tot_bins_obs = np.sum(n_binned_obs) #Actual number in the catalogue

n_binned_snr_theory = np.sum(n_binned_theory,axis=0) #Theoretical prediction for the number counts in the selection mass observable bins (i.e., summed over all redshifts)
n_binned_snr_obs = np.sum(n_binned_obs,axis=0) #Actual number in the catalogue of the same quantity.

n_binned_z_theory = np.sum(n_binned_theory,axis=1) #Theoretical prediction for the number counts in the redshift bins (i.e., summer over the selection mass observable)
n_binned_z_obs = np.sum(n_binned_obs,axis=1) #Actual number in the catalogue of the same quantity

#You can now print the numbers

print("")
print("Predicted total number of clusters in the bins = ",n_tot_bins_theory,"+-",np.sqrt(n_tot_bins_theory))
print("Observed total number of clusters in the bins = ",n_tot_bins_obs)

```

To plot the binned cluster abundance:

```python
size_marker = 1

pl.errorbar(bins_centres_snr,n_binned_snr_theory,yerr=np.sqrt(n_binned_snr_theory),color="tab:blue",
            linestyle="none",fmt="",capsize=size_marker)
pl.scatter(bins_centres_snr,n_binned_snr_obs,color="tab:orange",s=size_marker)
pl.xlabel("$SNR$")
pl.ylabel("$N$")
pl.axvline(x=5.,color="k")
pl.axhline(y=0.,color="k")
pl.title("Cluster counts across SNR (selection observable)")
pl.show()

pl.errorbar(bins_centres_z,n_binned_z_theory,yerr=np.sqrt(n_binned_z_theory),color="tab:blue",
            linestyle="none",fmt="",capsize=size_marker)
pl.scatter(bins_centres_z,n_binned_z_obs,color="tab:orange",s=size_marker)
pl.xlabel("$z$")
pl.ylabel("$N$")
pl.axvline(x=0.,color="k")
pl.axhline(y=0.,color="k")
pl.title("Cluster abundance across redshift")
pl.show()
```

Alternatively, if you are only interested in the binned number counts across either the selection observable or redshift, they can be computed directly by setting the parameter "binned_lik_type" to either "obs_select" or "z".


## How to access the cluster catalogue

In order to access the cluster catalogue values, do this:

```python
catalogue = number_counts.catalogue

q_obs = catalogue.catalogue["q_so_sim"] #SZ signal-to-noise values of the clusters in the catalogue
p_obs = catalogue.catalogue["p_so_sim"] #CMB lensing signal-to-noise values of the clusters in the catalogue
z = catalogue.catalogue["z"] #Redshift values of the clusters in the catalogue
```

## How to compute the stacked observable

cosmocnc can compute the expected value of any of the stacked observables and its (co)variance. In our Simons Observatory-like catalogue, this is the stacked CMB lensing signal-to-noise. In order to do this, do the following:


```python
cnc_params["likelihood_type"] = "unbinned" #Setting the likelihood type as unbinned
cnc_params["observables"] = [["q_so_sim"]] #Setting that there is only one cluster-by-cluster mass observable (the SZ signal-to-noise)
cnc_params["data_lik_from_abundance"] = False #So this parameter to this value so that the backward convolutional approach is followed (necessary for the stacked likelihood)
cnc_params["stacked_likelihood"] = True #Set this to compute the stacked likelihood
cnc_params["stacked_data"] = ["p_so_sim_stacked"] #List of stacked data set (only one here)
cnc_params["compute_stacked_cov"] = True #Whether to compute the stacked covariance, here set to true.

number_counts.cnc_params = cnc_params

number_counts.initialise()
log_lik = number_counts.get_log_lik() #By doing this we compute the stacked likelihood.
```

Next, in order to retrieved the stacked observable values, do this:

```python
p_stacked_obs = number_counts.catalogue.stacked_data["p_so_sim_stacked"]["data_vec"] #Empirical value of the stacked observable (here the stacked CMB lensing signal-to-noise)
p_stacked_theory = number_counts.stacked_model["p_so_sim_stacked"] #Predicted value of the stacked observable (here the stacked CMB lensing signal-to-noise).
p_stacked_std = np.sqrt(number_counts.stacked_variance["p_so_sim_stacked"]) #Predicted value of the standard deviation of the stacked observable (here the stacked CMB lensing signal-to-noise).

#We now print the values:

print("Predicted stacked observable =",p_stacked_theory,"+-",p_stacked_std)
print("Observed stacked observable =",p_stacked_obs)
```

In this case, it is also possible to compute the value of the stacked observable (here the stacked CMB lensing signal-to-noise) from the catalogue itself by doing the following:


```python
p_stacked_obs_2 = np.mean(p_obs)
print("Observed stacked observable =",p_stacked_obs_2)
```

## How to evaluate the cluster number count likelihood

In order to evaluate the cluster number count likelihood for a set of values of a given parameter (here, as an example, the cosmological parameter "sigma_8"), do the following. Importantly, DO NOT initialise the likelihood every time you update the parameter, but rather use the method "update_params", as shown here:

```python
cnc_params["likelihood_type"] = "unbinned" #In this example you're computing the unbinned likelihood. In order to compute the binned likelihood, set this to "binned" instead. In order to compute the extreme value likelihood, set this to "extreme_value" instead.
cnc_params["observables"] = [["q_so_sim"]] #This means that there is only one cluster-by-cluster mass observable (here, the SZ signal-to-noise).
cnc_params["data_lik_from_abundance"] = True #Do this so that the forward convolutional approach is followed (faster). Mind that if there is more than one cluster-by-cluster mass observable you'll have to set this to False.
cnc_params["stacked_likelihood"] = False #We don't consider the stacked likelihood here. In order to do that, simply set this to True instead.

number_counts.cnc_params = cnc_params
number_counts.initialise() #You initialise cosmocnc

n = 20
sigma_8_vec = np.linspace(0.808,0.815,n) #Define the parameter values
log_lik = np.zeros(n)

for i in range(0,n):

    cosmo_params["sigma_8"] = sigma_8_vec[i] #You update the value in the parameter dictionary
    number_counts.update_params(cosmo_params,scal_rel_params) #You update the parameter dictionary within the number_counts instance.
    log_lik[i] = number_counts.get_log_lik() #You compute the log-likelihood

lik_vec = np.exp(log_lik-np.max(log_lik)) #You exponentiate the log-likelihood to get the likelihood, normalised to 1 at its peak.

cnc_params["sigma_8"] = 0.811 #Setting the parameter value to its original value.

```

To plot the likelihood value as a function of the parameter values, you do this:

```python
pl.plot(sigma_8_vec,lik_vec)
pl.axvline(x=0.811,linestyle="dashed",color="k") #Plotting here the true value of the parameter.
pl.xlabel("$\sigma_8$")
pl.ylabel("$\mathcal{L}$")
pl.show()
```

The same exercise can be done for the other likelihoods with minor modifications to the code. In particular:

 - Binned likelihood: set "likelihood_type" to "binned", and choose binning scheme through "binned_lik_type".
 - Unbinned likelihood with both the SZ and the CMB lensing mass observable: set "observables" to [["q_so_sim"],["p_so_sim"]] (if they belong to different correlation sets, which is the case here), or to [["q_so_sim","p_so_sim"]] (if they belong to the same correlation set).
 - Unbinned likelihood with the CMB lensing stacked observable: set "stacked_likelihood" to True and "data_lik_from_abundance" to False.
 - Extreme value likelihood: set "likelihood_type" to "extreme_value".

## How to perform a goodness of fit analysis with the modified Cash statistic

In order to evaluate the modified Cash goodness-of-fit statistic and compare its value with the theoretical prediction, you do the following. Note that here it is done for the default parameter values, but this statistic can be evaluated at any point in parameter space.


```python
number_counts.cnc_params = cnc_params
number_counts.initialise() #Initialise cosmocnc

C,C_mean,C_std = number_counts.get_c_statistic() #Evaluate the modified Cash statistic. The returned values are C (the value of the Cash statistic), its predicted mean (C_mean), and its predicted standard deviation (C_std)

#You can then print the predicted value of the Cash statistic and its observed value as follows:

print("Predicted C =",C_mean,"+-",C_std)
print("Observed C =",C)
```


## How to get an estimate for the mass of the clusters in the catalogue

In order to get an estimate for the mass of the clusters in the catalogue, you do the following:


```python
cnc_params["likelihood_type"] = "unbinned" #Setting the likelihood type to unbinned (needed to get mass estimates)
cnc_params["observables"] = [["q_so_sim"]] #Here you're using only one mass observable. You can add more though, as required by the input instructions.
cnc_params["data_lik_from_abundance"] = False #Setting this to False that the backward convolutional approach is followed (needed to get mass estimates).
cnc_params["get_masses"] = True

number_counts.cnc_params = cnc_params

number_counts.initialise() #Initialiss cosmocnc
number_counts.get_log_lik() #Compute the log-likelihood
number_counts.get_masses() #Compute the mass estimates

ln_mass_est = number_counts.cluster_lnM #These are the logarithms of the mass estimates
ln_mass_std = number_counts.cluster_lnM_std #These are predicted standard deviations of the logarithm of the mass estimates
```

Since you are using a synthetic catalogue, you also have access to the true cluster mass, which can be retrieved like this:


```python
mass_true = number_counts.catalogue.M #true masses of the clusters in the catalogue
```

You can then plot the estimated masses vs the true masses like this:

```python
m_x = mass_true
m_y = np.exp(ln_mass_est)

fig = pl.figure()#figsize=(11*cm,8*cm*2))
gs = fig.add_gridspec(1,1)#,hspace=0)
axs = gs.subplots()


axs.scatter(m_x,m_y,s=1)
x = np.linspace(np.min(m_x),np.max(m_y),100)
axs.plot(x,x,color="tab:orange")
axs.set_xscale("log")
axs.set_yscale("log")
axs.set_xlim([0.7,30.])
axs.set_ylim([0.7,30.])

custom_ticks = [1,2,3,5,7,10,20]

axs.set_xticks(custom_ticks)
axs.set_yticks(custom_ticks)
axs.set_xticklabels(custom_ticks)
axs.set_yticklabels(custom_ticks)
axs.set_ylabel("Mean inferred mass [$10^{14} M_{\odot}$]")
axs.set_xlabel("Input mass [$10^{14} M_{\odot}$]")

axs.set_title("Cluster mass estimation")

pl.show()
```

## How to compute the predicted maximum value of the selection mass observable in the catalogue

Finally, let's consider the "most extreme cluster", which we define as that with the largest value of the selection observable (here, the SZ signal-to-noise). In order to compute it, do the following:


```python
number_counts.initialise()
number_counts.get_log_lik_extreme_value() #Evaluate the extreme value log-likelihood
number_counts.eval_extreme_value_quantities() #Evaluate other required extreme value quantities.

snr_max_mean = number_counts.obs_select_max_mean #This is the prediction for the mean value of the maximum selection mass observable in the catalogue.
snr_max_std = number_counts.obs_select_max_std #This is the predicted standard deviation for snr_max_mean
snr_max_obs = np.max(q_obs) #This is the actual maximum selection mass observable value in the catalogue.

#You can print the results like this:

print("Predicted maximum SNR",snr_max_mean,"+-",snr_max_std)
print("Observed maximum SNR",snr_max_obs)
```
