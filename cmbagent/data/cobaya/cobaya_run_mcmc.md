# Setting up an mcmc run with cobaya

Here is the structure the code when we are asked about running an mcmc. 

First, we write a yaml file. Then we use subprocess.run to run cobaya. 

```python
import yaml

# Setting up a dictionary to represent the yaml structure
config = {
    'theory': {
        'my_theory': {
            'extra_args': None,
            'stop_at_error': True
        }
    },
    'likelihood': {
        'my_likelihood': None
    },
    'output': '/path/to/chains_directory/chains_root',
    'params': {
        'param1': {
            'prior': {
                'min': 0.0,
                'max': 10.0
            },
            'ref': {
                'dist': 'norm',
                'loc': 4.1267026,
                'scale': 1.0
            },
            'proposal': 0.1,
            'latex': 'param_1'
        },
        'param2': {
            'prior': {
                'min': 0.0,
                'max': 10.0
            },
            'ref': {
                'dist': 'norm',
                'loc': 6.4,
                'scale': 1.0
            },
            'proposal': 0.1,
            'latex': 'param_2'
        },
        'param3': {
            'prior': {
                'min': 0.0,
                'max': 10.0
            },
            'ref': {
                'dist': 'norm',
                'loc': 0.3,
                'scale': 1.0
            },
            'proposal': 0.1,
            'latex': 'param_3'
        }
    },
    'prior': {
        'param1_prior': "lambda param1: stats.norm.logpdf(param1, loc=1.0, scale=0.013)"
    },
    'sampler': {
        'mcmc': {
            'burn_in': 0,
            'max_tries': 10000,
            'covmat': '/path/to/covmats/my_covmat.covmat',
            'covmat_params': None,
            'proposal_scale': 1.9,
            'output_every': '60s',
            'learn_every': '40d',
            'learn_proposal': True,
            'learn_proposal_Rminus1_max': 100.0,
            'learn_proposal_Rminus1_max_early': 100.0,
            'max_samples': float('inf'),
            'Rminus1_stop': 0.01,
            'Rminus1_cl_stop': 0.2,
            'Rminus1_cl_level': 0.95,
            'Rminus1_single_split': 4,
            'measure_speeds': True,
            'oversample_power': 0.4,
            'oversample_thin': True
        }
    },
    'timing': False,
    'debug': False
}

# Serializing the dictionary to a yaml formatted string
yaml_str = yaml.dump(config, default_flow_style=False)

# Writing the yaml string to a file
with open('config.yaml', 'w') as file:
    file.write(yaml_str)
    
try:
    subprocess.run(['mpirun','-np','4','cobaya-run', 'config.yaml', '-f'])
except Exception as e:
    print(f"Error running evaluate mode: {e}")
```

The main idea is to have the `sampler` block set to `mcmc`.

Unless otherwise stated, we always specify `timing: False`.

We also set `debug: False` to avoid printing out execution messages.


## Setting up the covariance matrix

If no covmat file is specified, always use: 
```
covmat: auto 
```

Never use `covmat: null` or `covmat: None`. 