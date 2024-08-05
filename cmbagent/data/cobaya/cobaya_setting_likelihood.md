# How to set-up the likelihood block in cobaya

In the  yaml file the likelihood block is generally of the form:

```

likelihood: 

    likelihood_1:
        likelihood1_param1: value11
        likelihood1_param2: value12
        path: /path/to/likelihood1_package

    likelihood_2:
        likelihood2_param1: value21
        likelihood2_param2: value22
        version: v3

```

In this example we have set-up two likelihoods which depend on different parameters. If the likelihoods are well maintained, and PYTHONPATH or other environment variables correctly set, in principle, there should be no need to specify the path or other parameters to the likelihood.

Hence the likelihood may look as simple as:

```

likelihood: 

    likelihood_1:

    likelihood_2:

```

