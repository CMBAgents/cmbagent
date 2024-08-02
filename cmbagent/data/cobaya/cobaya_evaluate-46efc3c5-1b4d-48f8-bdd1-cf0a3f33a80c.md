# Setting up a yaml parameter file for cobaya evaluate mode

Here is the structure of the yaml file when we are asked about evaluating a likelihood at some parameter values.

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
    evaluate:
        override:
           param1 : 1.2
           param2 : 3.5
           param3 : 6.4

timing: True
debug : False 

```

The main idea is to have the sampler block set to

```yaml

sampler:
    evaluate:
        override:
           param1 : 1.2
           param2 : 3.5
           param3 : 6.4
```

Unless otherwise stated, we always specify

```yaml
timing: true
```

so we can see the evaluation time.

We also set `debug: False` to avoid printing out execution messages.

