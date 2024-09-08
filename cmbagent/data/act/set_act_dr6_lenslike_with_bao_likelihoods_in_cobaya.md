**EXAMPLE YAML configuration file for COBAYA to run ACT DR6 Lensing with Bao likelihoods**

This is bits in the yaml file relevant to the analysis for lcdm_actplanck_baseline_167926925701

We have simplified it to make it more general.

```

likelihood:

  act_dr6_lenslike.ACTDR6LensLike:
    mock: false
    nsims_act: 792.0
    nsims_planck: 400.0
    no_like_corrections: true
    lens_only: true
    trim_lmax: 2998
    variant: actplanck_baseline
    apply_hartlap: true
    limber: false
    nz: 100
    kmax: 10
    scale_cov: null
    type: []
    speed: -1
    stop_at_error: true
    version: null
    lmax: 4000
    input_params: []
    output_params: []
    
  bao.sdss_dr7_mgs:
    path: null
    prob_dist: bao_data/sdss_MGS_prob.txt
    prob_dist_bounds:
    - 0.8005
    - 1.1985
    rs_rescale: 4.29720761315
    data:
    - 0.15
    - 4.465666824
    - 0.1681350461
    - DV_over_rs
    aliases:
    - BAO
    speed: 5000
    type: BAO
    measurements_file: null
    rs_fid: null
    cov_file: null
    invcov_file: null
    redshift: null
    observable_1: null
    observable_2: null
    observable_3: null
    grid_file: null
    stop_at_error: false
    version: null
    input_params: []
    output_params: []
  bao.sixdf_2011_bao:
    path: null
    rs_rescale: 1.027369826
    data:
    - 0.106
    - 0.336
    - 0.015
    - rs_over_DV
    aliases:
    - BAO
    speed: 5000
    type: BAO
    measurements_file: null
    rs_fid: null
    prob_dist: null
    cov_file: null
    invcov_file: null
    redshift: null
    observable_1: null
    observable_2: null
    observable_3: null
    grid_file: null
    stop_at_error: false
    version: null
    input_params: []
    output_params: []
  bao.sdss_dr12_lrg_bao_dmdh:
    path: null
    measurements_file: bao_data/sdss_DR12_LRG_BAO_DMDH.dat
    cov_file: bao_data/sdss_DR12_LRG_BAO_DMDH_covtot.txt
    rs_fid: 1
    aliases:
    - BAO
    speed: 2000
    type: BAO
    rs_rescale: null
    prob_dist: null
    invcov_file: null
    redshift: null
    observable_1: null
    observable_2: null
    observable_3: null
    grid_file: null
    stop_at_error: false
    version: null
    input_params: []
    output_params: []
  bao.sdss_dr16_lrg_bao_dmdh:
    path: null
    measurements_file: bao_data/sdss_DR16_LRG_BAO_DMDH.dat
    cov_file: bao_data/sdss_DR16_LRG_BAO_DMDH_covtot.txt
    rs_fid: 1
    aliases:
    - BAO
    speed: 2000
    type: BAO
    rs_rescale: null
    prob_dist: null
    invcov_file: null
    redshift: null
    observable_1: null
    observable_2: null
    observable_3: null
    grid_file: null
    stop_at_error: false
    version: null
    input_params: []
    output_params: []
    

params:
  logA:
    latex: \log(10^{10} A_\mathrm{s})
    prior:
      max: 4.0
      min: 1.61
    proposal: 0.001
    ref:
      dist: norm
      loc: 3.05
      scale: 0.001
  ns:
    latex: n_\mathrm{s}
    prior:
      dist: norm
      loc: 0.96
      scale: 0.02
    proposal: 0.002
    ref:
      dist: norm
      loc: 0.965
      scale: 0.004
  s8h5:
    derived: 'lambda sigma8, H0: sigma8*(H0*1e-2)**(-0.5)'
    latex: \sigma_8/h^{0.5}
  s8omegamp25:
    derived: 'lambda sigma8, omegam: sigma8*omegam**0.25'
    latex: \sigma_8 \Omega_\mathrm{m}^{0.25}
  s8omegamp5:
    derived: 'lambda sigma8, omegam: sigma8*omegam**0.5'
    latex: \sigma_8 \Omega_\mathrm{m}^{0.5}
  S825:
    derived: 'lambda sigma8, omegam: sigma8*(omegam/0.3)**0.25'
    latex: \sigma_8 (\Omega_\mathrm{m}/0.3)^{0.25}
  S85:
    derived: 'lambda sigma8, omegam: sigma8*(omegam/0.3)**0.5'
    latex: \sigma_8 (\Omega_\mathrm{m}/0.3)^{0.5}
  sigma8:
    latex: \sigma_8
    derived: true
  ombh2:
    latex: \Omega_\mathrm{b} h^2
    prior:
      dist: norm
      loc: 0.02233
      scale: 0.00036
    proposal: 0.0001
    ref:
      dist: norm
      loc: 0.0224
      scale: 0.0001
    renames:
    - omegabh2
  omch2:
    latex: \Omega_\mathrm{c} h^2
    prior:
      max: 0.99
      min: 0.005
    proposal: 0.0005
    ref:
      dist: norm
      loc: 0.12
      scale: 0.001
  omegam:
    latex: \Omega_\mathrm{m}
    derived: true
  omegamh2:
    derived: 'lambda omegam, H0: omegam*(H0/100)**2'
    latex: \Omega_\mathrm{m} h^2
  H0:
    latex: H_0
    prior:
        max: 100
        min: 40
    proposal: 1.
    ref:
      dist: norm
      loc: 67.
      scale: 1.
      
  chi2__BAO:
    latex: \chi^2_\mathrm{BAO}
    derived: true
```

