# Setting up a yaml parameter file for cobaya mcmc mode

Here is the structure of the yaml file when we are asked about determining posterior distributions using a likelihood. If one is simply evaluating a likelihood at some parameter values, they should not use MCMC. Instead, they should use `evaluate`.

```yaml
theory:
  my_theory:
    extra_args:
    stop_at_error: true


likelihood:
  my_likelihood:

output: /path/to/chains_directory/chains_root

params:
    param1:
      prior:
        min: 0.0
        max: 10.0
      ref:
        dist: norm
        loc: 4.1267026E+00
        scale: 1.0
      proposal: 0.1
      latex: param_1

    param2:
      prior:
        min: 0.0
        max: 10.0
      ref:
        dist: norm
        loc: 6.4
        scale: 1.0
      proposal: 0.1
      latex: param_2

    param3:
      prior:
        min: 0.0
        max: 10.0
      ref:
        dist: norm
        loc: 3e-1
        scale: 1.0
      proposal: 0.1
      latex: param_3

prior:
    param1_prior: "lambda param1: stats.norm.logpdf(param1, loc=1.0, scale=0.013)"
 

sampler:
    mcmc:
        burn_in: 0
        max_tries: 10000
        covmat: /path/to/covmats/my_covmat.covmat
        covmat_params: null
        proposal_scale: 1.9
        output_every: 60s
        learn_every: 40d
        learn_proposal: true
        learn_proposal_Rminus1_max: 100.0
        learn_proposal_Rminus1_max_early: 100.0
        max_samples: .inf
        Rminus1_stop: 0.01
        Rminus1_cl_stop: 0.2
        Rminus1_cl_level: 0.95
        Rminus1_single_split: 4
        measure_speeds: true
        oversample_power: 0.4
        oversample_thin: true

timing: True
debug : False 

```

The main idea is to have the `sampler` block set to

```yaml
  mcmc:
    burn_in: 0
    max_tries: 10000
    covmat: /path/to/covmats/my_covmat.covmat
    covmat_params: null
    proposal_scale: 1.9
    output_every: 60s
    learn_every: 40d
    learn_proposal: true
    learn_proposal_Rminus1_max: 100.0
    learn_proposal_Rminus1_max_early: 100.0
    max_samples: .inf
    Rminus1_stop: 0.01
    Rminus1_cl_stop: 0.2
    Rminus1_cl_level: 0.95
    Rminus1_single_split: 4
    measure_speeds: true
    oversample_power: 0.4
    oversample_thin: true
```

Unless otherwise stated, we always specify

```yaml
timing: False
```

so we the evaluation time is not printed to avoid printing too many messages.

We also set `debug: False` to avoid printing out execution messages.

